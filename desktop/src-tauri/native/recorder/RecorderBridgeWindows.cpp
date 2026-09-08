#define NOMINMAX
#include <windows.h>
#include <mmdeviceapi.h>
#include <audioclient.h>
#include <shellapi.h>
#include <wrl/client.h>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <iomanip>
#include <future>
#include <memory>
#include <mutex>
#include <sstream>
#include <thread>
#include "RecorderBridge.h"
#include "WindowsTimeline.h"

using Microsoft::WRL::ComPtr;
namespace {
std::mutex gate;
std::condition_variable ended;
bool active = false;
std::atomic<bool> stop_requested{false};
std::atomic<int> sleep_reason{0};
std::atomic<bool> watching{false};
WhosaidPowerCallback power_sink = nullptr;
void* power_context = nullptr;

double now() {
    return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
}
uint64_t qpc100ns() {
    LARGE_INTEGER ticks, frequency; QueryPerformanceCounter(&ticks); QueryPerformanceFrequency(&frequency);
    return uint64_t(static_cast<long double>(ticks.QuadPart) * 10000000 / frequency.QuadPart);
}
std::string quoted(const std::string& s) {
    std::ostringstream out; out << '"';
    for (unsigned char c : s) {
        if (c == '"' || c == '\\') out << '\\' << c;
        else if (c < 32) out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << unsigned(c) << std::dec;
        else out << c;
    }
    return out.str() + '"';
}
struct AudioError : std::runtime_error {
    HRESULT code;
    explicit AudioError(HRESULT hr) : std::runtime_error([hr] {
        std::ostringstream s; s << "声音设备无法采集，请检查默认设备、麦克风隐私权限和其他应用的独占设置（WASAPI 0x" << std::hex << uint32_t(hr) << "）"; return s.str();
    }()), code(hr) {}
};
void check(HRESULT hr) {
    if (FAILED(hr)) throw AudioError(hr);
}
struct ComScope {
    HRESULT result = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    ComScope() { check(result); }
    ~ComScope() { CoUninitialize(); }
};

struct Capture {
    ComPtr<IAudioClient> client;
    ComPtr<IAudioCaptureClient> reader;
    std::wstring id;
    ~Capture() { if (client) client->Stop(); }
    void open(IMMDeviceEnumerator* devices, bool system) {
        ComPtr<IMMDevice> device;
        check(devices->GetDefaultAudioEndpoint(system ? eRender : eCapture, eConsole, &device));
        LPWSTR value = nullptr; check(device->GetId(&value)); id = value; CoTaskMemFree(value);
        check(device->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr, &client));
        WAVEFORMATEX format{};
        format.wFormatTag = WAVE_FORMAT_IEEE_FLOAT; format.nChannels = 1;
        format.nSamplesPerSec = 48000; format.wBitsPerSample = 32;
        format.nBlockAlign = 4; format.nAvgBytesPerSec = 192000;
        DWORD flags = AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY;
        if (system) flags |= AUDCLNT_STREAMFLAGS_LOOPBACK;
        check(client->Initialize(AUDCLNT_SHAREMODE_SHARED, flags, 1000000, 0, &format, nullptr));
        check(client->GetService(IID_PPV_ARGS(&reader)));
        check(client->Start());
    }
    bool changed(IMMDeviceEnumerator* devices, bool system) {
        ComPtr<IMMDevice> device;
        if (FAILED(devices->GetDefaultAudioEndpoint(system ? eRender : eCapture, eConsole, &device))) return true;
        LPWSTR value = nullptr;
        if (FAILED(device->GetId(&value))) return true;
        bool different = id != value; CoTaskMemFree(value); return different;
    }
    float drain(whosaid::WindowsTimeline& track, uint64_t epoch) {
        float peak = 0;
        // Bound work so a pathological driver cannot starve stop/power events.
        for (int i = 0; i < 100; ++i) {
            UINT32 next = 0; check(reader->GetNextPacketSize(&next)); if (!next) break;
            BYTE* data = nullptr; UINT32 count = 0; DWORD flags = 0; UINT64 position = 0, timestamp = 0;
            check(reader->GetBuffer(&data, &count, &flags, &position, &timestamp));
            try {
                if (flags & AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR) {
                    // Arrival time minus packet duration is a conservative fallback.
                    auto current = qpc100ns(); auto duration = uint64_t(count) * 10000000 / 48000;
                    timestamp = current > duration ? current - duration : epoch;
                }
                uint64_t target = timestamp > epoch ? uint64_t((timestamp - epoch) * 48000.0 / 10000000) : 0;
                peak = std::max(peak, track.append(target, reinterpret_cast<float*>(data), count,
                                                   (flags & AUDCLNT_BUFFERFLAGS_SILENT) || !data));
            } catch (...) { reader->ReleaseBuffer(count); throw; }
            check(reader->ReleaseBuffer(count));
        }
        return peak;
    }
};

void manifest(const std::filesystem::path& dir, double started) {
    auto temp = dir / "session.json.tmp";
    std::ofstream out(temp, std::ios::binary); out.exceptions(std::ios::badbit | std::ios::failbit);
    out << "{\"schemaVersion\":1,\"sessionId\":" << quoted(dir.filename().u8string())
        << ",\"startedAt\":" << std::setprecision(17) << started
        << ",\"systemTrack\":\"system.caf\",\"microphoneTrack\":\"microphone.caf\","
           "\"systemStatus\":\"active\",\"microphoneStatus\":\"pending\",\"complete\":false}";
    out.close();
    if (!MoveFileExW(temp.c_str(), (dir / "session.json").c_str(), MOVEFILE_WRITE_THROUGH))
        throw std::runtime_error("Cannot persist recording manifest");
}

void record(std::filesystem::path dir, WhosaidRecorderCallback callback, void* context) {
    auto emit = [&](const std::string& event) { callback(event.c_str(), context); };
    auto source = [&](bool system, const char* status) {
        emit("{\"type\":\"source_status\",\"source\":\"" + std::string(system ? "system" : "microphone") + "\",\"status\":" + quoted(status) + "}");
    };
    std::string terminal;
    try {
        ComScope com;
        ComPtr<IMMDeviceEnumerator> devices; check(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL, IID_PPV_ARGS(&devices)));
        double started = now(); uint64_t epoch = qpc100ns();
        whosaid::WindowsTimeline system_track(dir / "system.caf"), mic_track(dir / "microphone.caf");
        manifest(dir, started);
        emit("{\"type\":\"starting\"}");
        std::unique_ptr<Capture> streams[2];
        // System capture must initialize successfully; mic may recover independently.
        streams[0] = std::make_unique<Capture>(); streams[0]->open(devices.Get(), true);
        bool mic_denied = false;
        try { streams[1] = std::make_unique<Capture>(); streams[1]->open(devices.Get(), false); }
        catch (const AudioError& e) { mic_denied = e.code == E_ACCESSDENIED; streams[1].reset(); }
        emit("{\"type\":\"recording\",\"startedAt\":" + std::to_string(started) + "}");
        source(true, "active"); source(false, streams[1] ? "active" : mic_denied ? "denied" : "unavailable");
        uint64_t last_check = 0, last_level = 0, last_elapsed = UINT64_MAX, unavailable_since = 0;
        float peaks[2] = {};
        while (!stop_requested.load()) {
            auto elapsed = qpc100ns() - epoch;
            bool retry = elapsed - last_check >= 10000000;
            if (retry) last_check = elapsed;
            for (int i = 0; i < 2; ++i) {
                if (retry && streams[i] && streams[i]->changed(devices.Get(), i == 0)) {
                    streams[i].reset(); source(i == 0, "interrupted");
                }
                if (retry && !streams[i]) {
                    try { streams[i] = std::make_unique<Capture>(); streams[i]->open(devices.Get(), i == 0); source(i == 0, "active"); }
                    catch (const AudioError& e) { streams[i].reset(); source(i == 0, e.code == E_ACCESSDENIED ? "denied" : "unavailable"); }
                }
                if (streams[i]) {
                    try { peaks[i] = std::max(peaks[i], streams[i]->drain(i == 0 ? system_track : mic_track, epoch)); }
                    catch (const std::ios_base::failure&) { throw; }
                    catch (...) { streams[i].reset(); source(i == 0, "interrupted"); }
                }
            }
            if (!streams[0]) {
                if (!unavailable_since) unavailable_since = elapsed;
                if (elapsed - unavailable_since > 100000000) throw std::runtime_error("电脑声音连续 10 秒无法恢复，已结束采集并保留音轨，请恢复保存后检查输出设备");
            } else unavailable_since = 0;
            if (elapsed - last_level >= 1000000) {
                last_level = elapsed;
                for (int i = 0; i < 2; ++i) {
                    emit("{\"type\":\"audio_level\",\"source\":\"" + std::string(i == 0 ? "system" : "microphone") +
                         "\",\"peak\":" + std::to_string(peaks[i]) + ",\"sampledAt\":" + std::to_string(now()) + "}"); peaks[i] = 0;
                }
            }
            if (elapsed / 10000000 != last_elapsed) {
                last_elapsed = elapsed / 10000000;
                // Loopback supplies no packets during silence. Preserve time with a
                // one-second cushion for delayed packets, on both independent tracks.
                auto padded = elapsed > 10000000 ? uint64_t((elapsed - 10000000) * 48000.0 / 10000000) : 0;
                system_track.silence_to(padded); mic_track.silence_to(padded);
                system_track.flush(); mic_track.flush();
                emit("{\"type\":\"elapsed\",\"elapsedSeconds\":" + std::to_string(last_elapsed) + "}");
            }
            Sleep(10);
        }
        int reason = sleep_reason.load();
        if (reason) emit("{\"type\":\"suspending\",\"reason\":\"" + std::string(reason == 1 ? "display_sleep" : "system_sleep") + "\"}");
        uint64_t end = uint64_t((qpc100ns() - epoch) * 48000.0 / 10000000);
        for (int i = 0; i < 2; ++i) if (streams[i]) {
            try { streams[i]->drain(i == 0 ? system_track : mic_track, epoch); } catch (...) {}
            streams[i].reset();
        }
        // Do not pad a process-suspend gap if Windows delayed execution until wake.
        if (end <= std::max(system_track.size(), mic_track.size()) + 48000ULL * 5) {
            system_track.silence_to(end); mic_track.silence_to(end);
        }
        system_track.flush(); mic_track.flush();
        terminal = "{\"type\":\"stopped\",\"sessionDir\":" + quoted(dir.u8string()) +
            ",\"systemTrack\":" + quoted((dir / "system.caf").u8string()) +
            ",\"microphoneTrack\":" + quoted((dir / "microphone.caf").u8string()) + "}";
    } catch (const std::exception& e) {
        terminal = "{\"type\":\"fatal_error\",\"message\":" + quoted(e.what()) + "}";
    } catch (...) { terminal = "{\"type\":\"fatal_error\",\"message\":\"Windows recording failed\"}"; }
    // Keep stop/start serialized until the terminal callback releases its context.
    { std::lock_guard<std::mutex> lock(gate); emit(terminal); active = false; }
    ended.notify_all();
    // Exactly one terminal event; no callbacks after this point.
}

void suspend(int reason) {
    std::unique_lock<std::mutex> lock(gate);
    if (active) {
        sleep_reason.store(reason); stop_requested.store(true);
        ended.wait_for(lock, std::chrono::milliseconds(1200), [] { return !active; });
    }
}
LRESULT CALLBACK power_window(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    if (message == WM_POWERBROADCAST) {
        if (wparam == PBT_APMSUSPEND) {
            if (power_sink) power_sink(3, power_context); suspend(2);
        } else if (wparam == PBT_APMRESUMEAUTOMATIC) {
            if (power_sink) power_sink(4, power_context);
        } else if (wparam == PBT_POWERSETTINGCHANGE) {
            auto setting = reinterpret_cast<POWERBROADCAST_SETTING*>(lparam);
            if (setting->PowerSetting == GUID_CONSOLE_DISPLAY_STATE && setting->DataLength == sizeof(DWORD)) {
                DWORD value; std::memcpy(&value, setting->Data, sizeof(value));
                if (value == 0) { suspend(1); if (power_sink) power_sink(1, power_context); }
                else if (value == 1 && power_sink) power_sink(2, power_context);
            }
        }
        return TRUE;
    }
    return DefWindowProcW(window, message, wparam, lparam);
}
}

extern "C" {
int32_t whosaid_recorder_api_version() { return 1; }
char* whosaid_recorder_permission_snapshot() {
    // Desktop WASAPI has no macOS-style permission prompt; activation is authoritative.
    return _strdup("{\"systemAudio\":\"granted\",\"microphone\":\"notDetermined\"}");
}
void whosaid_recorder_free_string(char* value) { free(value); }
void whosaid_recorder_open_settings(int32_t pane) {
    ShellExecuteW(nullptr, L"open", pane == 2 ? L"ms-settings:privacy-microphone" : L"ms-settings:sound", nullptr, nullptr, SW_SHOWNORMAL);
}
int32_t whosaid_recorder_start(const char* path, WhosaidRecorderCallback callback, void* context) {
    std::lock_guard<std::mutex> lock(gate);
    if (active || !path || !callback) return -1;
    try {
        auto dir = std::filesystem::u8path(path);
        stop_requested.store(false); sleep_reason.store(0); active = true;
        std::thread(record, dir, callback, context).detach(); return 0;
    } catch (...) { active = false; return -1; }
}
int32_t whosaid_recorder_stop() {
    std::lock_guard<std::mutex> lock(gate);
    if (!active) return -1;
    stop_requested.store(true); return 0;
}
int32_t whosaid_recorder_watch_power_events(WhosaidPowerCallback callback, void* context) {
    if (watching.exchange(true)) return -1;
    try {
        // Startup handshake: report failures instead of silently missing sleep events.
        auto ready = std::make_shared<std::promise<bool>>(); auto result = ready->get_future();
        std::thread([callback, context, ready] {
            power_sink = callback; power_context = context;
            WNDCLASSW cls{}; cls.lpfnWndProc = power_window; cls.hInstance = GetModuleHandleW(nullptr); cls.lpszClassName = L"WhosaidPowerMonitor";
            RegisterClassW(&cls);
            HWND window = CreateWindowExW(0, cls.lpszClassName, L"", 0, 0, 0, 0, 0, nullptr, nullptr, cls.hInstance, nullptr);
            auto notification = window ? RegisterPowerSettingNotification(window, &GUID_CONSOLE_DISPLAY_STATE, DEVICE_NOTIFY_WINDOW_HANDLE) : nullptr;
            if (!notification) { if (window) DestroyWindow(window); ready->set_value(false); return; }
            ready->set_value(true);
            MSG msg; while (GetMessageW(&msg, nullptr, 0, 0) > 0) { TranslateMessage(&msg); DispatchMessageW(&msg); }
            UnregisterPowerSettingNotification(notification); DestroyWindow(window);
        }).detach();
        if (result.get()) return 0;
    } catch (...) {}
    watching.store(false); return -1;
}
}
