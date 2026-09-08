#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <vector>

namespace whosaid {
// CAF supports unknown data length, so a crash does not leave a stale WAV size.
// Both tracks use the same QPC epoch and 48 kHz mono float PCM timeline.
class WindowsTimeline {
    std::ofstream file;
    uint64_t frames = 0;
    void be(uint64_t value, unsigned bytes) {
        for (int i = static_cast<int>(bytes) - 1; i >= 0; --i) file.put(char(value >> (i * 8)));
    }
public:
    explicit WindowsTimeline(const std::filesystem::path& path) {
        if (std::filesystem::exists(path)) throw std::runtime_error("Audio track already exists");
        file.exceptions(std::ios::badbit | std::ios::failbit);
        file.open(path, std::ios::binary);
        file.write("caff", 4); be(1, 2); be(0, 2);
        file.write("desc", 4); be(32, 8);
        double rate = 48000; uint64_t bits; std::memcpy(&bits, &rate, 8); be(bits, 8);
        file.write("lpcm", 4); be(1 | 2, 4); // float, little endian (CAF flags)
        be(4, 4); be(1, 4); be(1, 4); be(32, 4);
        file.write("data", 4); be(UINT64_MAX, 8); be(0, 4);
        flush();
    }
    uint64_t size() const { return frames; }
    void silence_to(uint64_t target) {
        const float zeros[4800] = {};
        while (frames < target) {
            auto n = std::min<uint64_t>(target - frames, 4800);
            file.write(reinterpret_cast<const char*>(zeros), std::streamsize(n * 4)); frames += n;
        }
    }
    float append(uint64_t target, const float* samples, uint32_t count, bool silent) {
        if (target > frames + 48000ULL * 60) throw std::runtime_error("Audio timestamp jumped");
        silence_to(target);
        uint64_t skip = std::min<uint64_t>(frames - target, count);
        float peak = 0;
        std::vector<float> clean(count - skip, 0);
        for (size_t i = 0; i < clean.size(); ++i) {
            float v = silent ? 0 : samples[i + skip];
            clean[i] = std::isfinite(v) ? std::clamp(v, -1.0f, 1.0f) : 0;
            peak = std::max(peak, std::abs(clean[i]));
        }
        if (!clean.empty()) file.write(reinterpret_cast<const char*>(clean.data()), std::streamsize(clean.size() * 4));
        frames += clean.size(); return peak;
    }
    void flush() { file.flush(); }
};
}
