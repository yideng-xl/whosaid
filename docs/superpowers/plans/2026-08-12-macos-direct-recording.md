# macOS 直接录音 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 macOS Apple Silicon 的 whosaid 内录制全系统声音和可降级的麦克风，停止后保存 `.m4a` 并自动进入现有分人、转写流程。

**Architecture:** 在 Tauri 主进程内链接 Objective-C++ 原生桥，分别用 ScreenCaptureKit 和 AVAudioEngine 写双轨 CAF；Rust 协调器管理状态、恢复清单和 FFmpeg 混音；Svelte 前端呈现录音状态并复用现有 `submitJob(audioPath)`。录音半成品不进入 Python 任务队列，只有最终音频落盘后才创建任务。

**Tech Stack:** macOS 13+、Objective-C++17、ScreenCaptureKit、AVFoundation/AVAudioEngine、CoreMedia、Rust/Tauri 2、FFmpeg、Svelte 5、TypeScript、Vitest。

## Global Constraints

- 仅支持 Apple Silicon 和 macOS 13 及以上版本；Windows 构建必须继续成功且不包含录音功能。
- 录制全系统声音，不保存屏幕帧、图像或视频。
- 系统声音是主通道；麦克风拒绝、占用、断开或启动失败时降级为仅系统声音。
- 首版只有“开始”和“停止并开始转写”，不实现暂停、继续、实时转写、回声消除或应用筛选。
- 最终录音为 48 kHz AAC-LC `.m4a`；临时轨道为线性 PCM CAF。
- 系统音频中途失败不得自动提交为正常任务；混音失败保留双轨，提交失败保留最终音频。
- 同时最多一个录音会话；所有路径必须由应用数据目录派生，不能接受前端传入的任意输出目录。
- 修改 `/Applications/whosaid.app` 前必须重新深度签名，并在启动前后执行严格签名验证。

## File Map

- Create `desktop/src-tauri/native/recorder/RecorderBridge.h`：Rust 可调用的稳定 C ABI。
- Create `desktop/src-tauri/native/recorder/RecorderBridge.mm`：ScreenCaptureKit、AVAudioEngine、权限与会话控制。
- Create `desktop/src-tauri/native/recorder/TimelineWriter.h`：共同时间轴写入接口。
- Create `desktop/src-tauri/native/recorder/TimelineWriter.mm`：首帧偏移、缺口补静音、CAF 写入。
- Create `desktop/src-tauri/native/recorder/tests/RecorderNativeTests.mm`：不访问真实设备的原生逻辑测试。
- Create `desktop/scripts/test-recorder-native.sh`：编译并运行原生测试。
- Modify `desktop/src-tauri/build.rs`：仅 macOS 编译 Objective-C++ 并链接系统框架。
- Modify `desktop/src-tauri/Cargo.toml`：增加 macOS 原生桥构建依赖。
- Create `desktop/src-tauri/src/recording/native.rs`：C ABI 和回调边界。
- Create `desktop/src-tauri/src/recording/state.rs`：单会话状态机与可序列化快照。
- Create `desktop/src-tauri/src/recording/storage.rs`：录音目录、清单和遗留会话扫描。
- Create `desktop/src-tauri/src/recording/mix.rs`：FFmpeg 参数生成、原子落盘与混音结果。
- Create `desktop/src-tauri/src/recording/mod.rs`：协调器、Tauri 命令和事件。
- Modify `desktop/src-tauri/src/lib.rs`：注册状态、命令、退出拦截和资源路径。
- Modify `desktop/src-tauri/tauri.conf.json`：macOS 13 门槛、用途说明和资源配置。
- Create `desktop/src-tauri/Info.plist`：系统录音和麦克风用途说明。
- Create `desktop/src/lib/recording.ts`：前端类型、Tauri 命令和事件封装。
- Create `desktop/src/lib/recordingState.ts`：前端停止、提交及错误状态归并。
- Create `desktop/src/lib/RecordingPanel.svelte`：录音与停止后三阶段界面。
- Create `desktop/src/lib/RecordingPanel.test.ts`：录音面板交互测试。
- Create `desktop/src/lib/recordingState.test.ts`：前端状态归并测试。
- Modify `desktop/src/lib/Sidebar.svelte`：增加“开始录音”入口。
- Modify `desktop/src/routes/+page.svelte`：连接录音、自动提交、恢复与退出确认。
- Modify `desktop/src/lib/Icon.svelte`：补录音相关图标（仅当现有图标集没有圆点/麦克风）。
- Modify `desktop/package.json`：增加原生测试命令。
- Modify `README.md`、`desktop/README.md`：更新能力、权限、系统门槛和验收步骤。

---

### Task 1: 建立主进程原生桥和权限归属

**Files:**
- Create: `desktop/src-tauri/native/recorder/RecorderBridge.h`
- Create: `desktop/src-tauri/native/recorder/RecorderBridge.mm`
- Create: `desktop/src-tauri/native/recorder/tests/RecorderNativeTests.mm`
- Create: `desktop/scripts/test-recorder-native.sh`
- Modify: `desktop/src-tauri/build.rs`
- Modify: `desktop/src-tauri/Cargo.toml`
- Modify: `desktop/package.json`

**Interfaces:**
- Consumes: Tauri 主应用进程和 `com.yideng.whosaid` 的 macOS 权限身份。
- Produces: `whosaid_recorder_api_version()`、`whosaid_recorder_permission_snapshot()`、`whosaid_recorder_free_string()`、`whosaid_recorder_open_settings(int)`。

- [ ] **Step 1: 写原生桥链接失败测试**

在 `RecorderNativeTests.mm` 先引用尚不存在的 API：

```objc
#include "../RecorderBridge.h"
#include <cassert>
#include <cstring>

int main() {
    assert(whosaid_recorder_api_version() == 1);
    char *json = whosaid_recorder_permission_snapshot();
    assert(json != nullptr);
    assert(std::strstr(json, "systemAudio") != nullptr);
    assert(std::strstr(json, "microphone") != nullptr);
    whosaid_recorder_free_string(json);
    return 0;
}
```

- [ ] **Step 2: 运行测试并确认因缺接口失败**

Run: `cd desktop && ./scripts/test-recorder-native.sh`

Expected: FAIL，编译器报告找不到 `RecorderBridge.h` 或未定义符号。

- [ ] **Step 3: 定义稳定 C ABI 和最小权限实现**

`RecorderBridge.h` 的公开接口固定为：

```c
#pragma once
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef void (*WhosaidRecorderCallback)(const char *json, void *context);
int32_t whosaid_recorder_api_version(void);
char *whosaid_recorder_permission_snapshot(void);
void whosaid_recorder_open_settings(int32_t pane); /* 1=系统录音, 2=麦克风 */
int32_t whosaid_recorder_start(const char *session_dir,
                               WhosaidRecorderCallback callback,
                               void *context);
int32_t whosaid_recorder_stop(void);
void whosaid_recorder_free_string(char *value);

#ifdef __cplusplus
}
#endif
```

最小实现只完成版本、权限 JSON、设置跳转和字符串释放；启停暂时返回非零“不支持”。权限 JSON 使用固定值：

```json
{"systemAudio":"granted|denied|notDetermined","microphone":"granted|denied|notDetermined"}
```

`build.rs` 仅在 `CARGO_CFG_TARGET_OS=macos` 时用 `cc::Build` 编译 `.mm`，打开 ARC、C++17 和 blocks，并链接：

```rust
println!("cargo:rustc-link-lib=framework=ScreenCaptureKit");
println!("cargo:rustc-link-lib=framework=AVFoundation");
println!("cargo:rustc-link-lib=framework=CoreMedia");
println!("cargo:rustc-link-lib=framework=CoreAudio");
println!("cargo:rustc-link-lib=framework=AppKit");
println!("cargo:rustc-link-lib=framework=Foundation");
```

Windows 分支不能引用或编译任何 Apple 头文件。

- [ ] **Step 4: 运行原生测试、Rust 测试和 Windows 配置检查**

Run: `cd desktop && npm run test:native`

Expected: PASS。

Run: `cd desktop/src-tauri && cargo test`

Expected: PASS，现有 Rust 测试不回退。

Run: `rg -n "RecorderBridge|ScreenCaptureKit" desktop/src-tauri/tauri.windows.conf.json desktop/scripts/build-runtime-windows.ps1`

Expected: 无 Windows 资源引用。

- [ ] **Step 5: 提交原生桥骨架**

```bash
git add desktop/src-tauri/native desktop/scripts/test-recorder-native.sh \
  desktop/src-tauri/build.rs desktop/src-tauri/Cargo.toml desktop/package.json
git commit -m "build: 接入 macOS 原生录音桥"
```

---

### Task 2: 实现共同时间轴和系统声音主轨

**Files:**
- Create: `desktop/src-tauri/native/recorder/TimelineWriter.h`
- Create: `desktop/src-tauri/native/recorder/TimelineWriter.mm`
- Modify: `desktop/src-tauri/native/recorder/RecorderBridge.mm`
- Modify: `desktop/src-tauri/native/recorder/tests/RecorderNativeTests.mm`

**Interfaces:**
- Consumes: 会话目录、48 kHz 单声道 PCM、单调时钟纳秒值。
- Produces: `WSTimelineWriter`，以及回调事件 `starting`、`recording`、`source_status`、`elapsed`、`fatal_error`。

- [ ] **Step 1: 写补静音和单会话失败测试**

在原生测试中增加纯函数断言：

```objc
assert(WSSilenceFrames(1'000'000'000, 1'250'000'000, 48'000) == 12'000);
assert(WSSilenceFrames(1'000'000'000, 999'000'000, 48'000) == 0);
```

- [ ] **Step 2: 运行原生测试并确认失败**

Run: `cd desktop && npm run test:native`

Expected: FAIL，`WSSilenceFrames` 尚不存在。

- [ ] **Step 3: 实现时间轴写入器**

`TimelineWriter.h` 暴露：

```objc
uint64_t WSSilenceFrames(uint64_t expectedHostNs,
                         uint64_t actualHostNs,
                         uint32_t sampleRate);

@interface WSTimelineWriter : NSObject
- (instancetype)initWithURL:(NSURL *)url
                  sampleRate:(double)sampleRate
                sessionStart:(uint64_t)sessionStartNs
                       error:(NSError **)error;
- (BOOL)appendBuffer:(AVAudioPCMBuffer *)buffer
          receivedAt:(uint64_t)hostNs
               error:(NSError **)error;
- (BOOL)close:(NSError **)error;
@end
```

第一帧按 `receivedAt - sessionStart` 写前置静音；后续按已写帧数与单调时间差补中间静音。小于一个音频缓冲的抖动不补，避免重复帧；负差值归零。

- [ ] **Step 4: 实现 ScreenCaptureKit 系统声音主轨**

在 `RecorderBridge.mm` 中：

```objc
SCStreamConfiguration *config = [SCStreamConfiguration new];
config.capturesAudio = YES;
config.excludesCurrentProcessAudio = YES;
config.sampleRate = 48000;
config.channelCount = 1;
config.width = 2;
config.height = 2;
config.minimumFrameInterval = CMTimeMake(1, 1);
```

选择主显示器创建 `SCContentFilter`，只注册 `SCStreamOutputTypeAudio`，绝不注册或写入 `SCStreamOutputTypeScreen`。音频回调把 `CMSampleBuffer` 转为 `AVAudioPCMBuffer` 后交给 `WSTimelineWriter`，写入 `<session>/system.caf`。

系统声音开始成功后发送：

```json
{"type":"recording","startedAt":1786500000.0}
{"type":"source_status","source":"system","status":"active"}
```

启动失败发送 `fatal_error` 并清理会话；中途停止发送 `fatal_error`，但先关闭已经写入的 CAF。

- [ ] **Step 5: 运行测试与静态隐私检查**

Run: `cd desktop && npm run test:native`

Expected: PASS。

Run: `rg -n "SCStreamOutputTypeScreen|AVAssetWriter.*video|\.mov|\.mp4" desktop/src-tauri/native/recorder`

Expected: 生产代码中无屏幕输出注册和视频写入路径。

- [ ] **Step 6: 提交系统声音主轨**

```bash
git add desktop/src-tauri/native/recorder
git commit -m "feat: 采集 macOS 系统声音"
```

---

### Task 3: 增加可降级麦克风和可恢复会话清单

**Files:**
- Modify: `desktop/src-tauri/native/recorder/RecorderBridge.mm`
- Modify: `desktop/src-tauri/native/recorder/tests/RecorderNativeTests.mm`

**Interfaces:**
- Consumes: Task 2 的 `WSTimelineWriter` 和系统主轨会话。
- Produces: 可选 `microphone.caf`、持续更新的 `session.json`、麦克风 `active|unavailable|denied|interrupted` 事件、`stopped` 事件。

- [ ] **Step 1: 写麦克风降级测试**

先把“麦克风错误不得杀死主轨”固化为原生纯逻辑：

```objc
assert(WSMicFailureIsFatal(WSMicStartResultDenied) == false);
assert(WSMicFailureIsFatal(WSMicStartResultUnavailable) == false);
assert(WSMicFailureIsFatal(WSMicStartResultInterrupted) == false);
```

原生会话对这三种结果只发送麦克风状态事件，不调用系统流的 stop。

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd desktop && npm run test:native`

Expected: FAIL，尚无麦克风依赖注入和降级状态。

- [ ] **Step 3: 实现 AVAudioEngine 麦克风轨**

使用默认 `inputNode`，先检查硬件格式：

```objc
AVAudioFormat *format = [engine.inputNode inputFormatForBus:0];
if (format.sampleRate <= 0 || format.channelCount == 0) {
    emitMicStatus(@"unavailable");
    return;
}
```

安装 tap，把缓冲转换为 48 kHz 单声道，交给独立 `WSTimelineWriter` 写
`microphone.caf`。权限拒绝发 `denied`；`engine.start` 失败发 `unavailable`；配置
变化或设备断开后停止麦克风 tap 并发 `interrupted`，不得调用系统流的 stop。

- [ ] **Step 4: 持续写会话清单并完成停止**

`session.json` 使用原子替换，最少包含：

```json
{
  "schemaVersion": 1,
  "sessionId": "uuid",
  "startedAt": 1786500000.0,
  "systemTrack": "system.caf",
  "microphoneTrack": "microphone.caf",
  "systemStatus": "active",
  "microphoneStatus": "interrupted",
  "complete": false
}
```

`whosaid_recorder_stop()` 先停系统流，再停麦克风，关闭两路 writer，最后回调：

```json
{"type":"stopped","sessionDir":"/data/recordings/.incomplete/abc","systemTrack":"/data/recordings/.incomplete/abc/system.caf","microphoneTrack":"/data/recordings/.incomplete/abc/microphone.caf"}
```

麦克风从未产出有效帧时，`microphoneTrack` 为 `null`。

- [ ] **Step 5: 运行原生测试**

Run: `cd desktop && npm run test:native`

Expected: PASS，拒绝、不可用、中断均不停止系统主轨。

- [ ] **Step 6: 提交麦克风降级**

```bash
git add desktop/src-tauri/native/recorder
git commit -m "feat: 添加可降级麦克风录制"
```

---

### Task 4: 建立 Rust 录音状态机和原生回调边界

**Files:**
- Create: `desktop/src-tauri/src/recording/native.rs`
- Create: `desktop/src-tauri/src/recording/state.rs`
- Create: `desktop/src-tauri/src/recording/mod.rs`
- Modify: `desktop/src-tauri/src/lib.rs`

**Interfaces:**
- Consumes: Task 1–3 的 C ABI 与 JSON 事件。
- Produces: `RecordingManager`、`RecordingSnapshot`、`start_recording`、`stop_recording`、`get_recording_state` 和 Tauri 事件 `recording://state`。

- [ ] **Step 1: 写状态机失败测试**

`state.rs` 先写：

```rust
#[test]
fn microphone_failure_does_not_fail_recording() {
    let mut state = RecordingState::new();
    state.apply(NativeEvent::Recording { started_at: 10.0 }).unwrap();
    state.apply(NativeEvent::SourceStatus {
        source: AudioSource::Microphone,
        status: SourceStatus::Unavailable,
    }).unwrap();
    assert_eq!(state.snapshot().phase, RecordingPhase::Recording);
    assert_eq!(state.snapshot().microphone, SourceStatus::Unavailable);
}

#[test]
fn duplicate_start_is_rejected() {
    let mut state = RecordingState::new();
    state.begin_start().unwrap();
    assert_eq!(state.begin_start().unwrap_err(), RecordingError::AlreadyRecording);
}
```

同时覆盖非法 stop、系统 `fatal_error`、`stopping → mixing → ready`。

- [ ] **Step 2: 运行 Rust 测试并确认失败**

Run: `cd desktop/src-tauri && cargo test recording::state`

Expected: FAIL，模块和类型尚不存在。

- [ ] **Step 3: 实现可序列化状态模型**

类型固定为：

```rust
#[derive(Clone, Debug, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum RecordingPhase {
    Idle, RequestingPermissions, Starting, Recording, Stopping, Mixing, Ready, Failed,
}

#[derive(Clone, Debug, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum SourceStatus { Pending, Active, Unavailable, Denied, Interrupted }

#[derive(Clone, Debug, Serialize)]
pub struct RecordingSnapshot {
    pub phase: RecordingPhase,
    pub elapsed_seconds: u64,
    pub system_audio: SourceStatus,
    pub microphone: SourceStatus,
    pub final_path: Option<String>,
    pub recoverable_paths: Vec<String>,
    pub error: Option<String>,
}
```

所有转移集中在 `RecordingState::apply`，Tauri command 不直接改字段。

- [ ] **Step 4: 封装 C ABI 回调**

`native.rs` 负责：

```rust
pub trait NativeRecorder: Send + Sync {
    fn permissions(&self) -> Result<PermissionSnapshot, RecordingError>;
    fn start(&self, session_dir: &Path,
             sink: Sender<NativeEvent>) -> Result<(), RecordingError>;
    fn stop(&self) -> Result<(), RecordingError>;
    fn open_settings(&self, pane: SettingsPane) -> Result<(), RecordingError>;
}
```

macOS 实现调用 C ABI；非 macOS 实现返回 `UnsupportedPlatform`，保证 Windows 编译。
回调第一时间复制 C 字符串并解析 JSON，不能把原生指针保存到回调之外。

- [ ] **Step 5: 实现协调器和 Tauri 事件**

`RecordingManager` 持有 `Mutex<RecordingState>`、原生实现和会话目录。每次状态
变化调用：

```rust
app.emit("recording://state", state.snapshot())
    .map_err(|e| RecordingError::Event(e.to_string()))?;
```

命令签名：

```rust
#[tauri::command]
pub fn start_recording(app: AppHandle,
                       manager: State<'_, RecordingManager>)
                       -> Result<RecordingSnapshot, String>;

#[tauri::command]
pub async fn stop_recording(app: AppHandle,
                            manager: State<'_, RecordingManager>)
                            -> Result<RecordingStopResult, String>;
```

本任务的 `stop_recording` 只走到 `stopping` 并返回原始轨道；Task 6 接上混音。

- [ ] **Step 6: 运行 Rust 全测并提交**

Run: `cd desktop/src-tauri && cargo test`

Expected: PASS。

```bash
git add desktop/src-tauri/src/recording desktop/src-tauri/src/lib.rs
git commit -m "feat: 添加录音状态协调器"
```

---

### Task 5: 实现录音目录、混音和故障恢复

**Files:**
- Create: `desktop/src-tauri/src/recording/storage.rs`
- Create: `desktop/src-tauri/src/recording/mix.rs`
- Modify: `desktop/src-tauri/src/recording/mod.rs`
- Modify: `desktop/src-tauri/src/lib.rs`
- Modify: `desktop/src-tauri/Cargo.toml`

**Interfaces:**
- Consumes: Task 3 的 `session.json` 和双轨路径、现有包内 FFmpeg 路径解析。
- Produces: `RecordingStopResult { final_path }`、`list_recoverable_recordings()`、`retry_recording_mix(session_id)`。

- [ ] **Step 1: 写路径与 FFmpeg 参数失败测试**

```rust
#[test]
fn final_name_is_collision_safe() {
    let store = RecordingStore::new(tempdir().unwrap().path().to_path_buf());
    let first = store.final_path_at("2026-08-12_10-30-15", |_| false);
    let second = store.final_path_at("2026-08-12_10-30-15", |p| p == first);
    assert_ne!(first, second);
    assert_eq!(first.extension().unwrap(), "m4a");
}

#[test]
fn mic_missing_builds_system_only_command() {
    let args = build_mix_args(Path::new("system.caf"), None, Path::new("out.tmp.m4a"));
    assert!(!args.iter().any(|x| x == "amix=inputs=2"));
    assert!(args.iter().any(|x| x == "-c:a"));
}
```

再覆盖双轨 `amix`、临时输出、失败不删输入、恢复扫描忽略 `complete=true`。

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd desktop/src-tauri && cargo test recording::`

Expected: FAIL，模块尚不存在。

- [ ] **Step 3: 实现受控录音目录**

```rust
pub struct RecordingStore { root: PathBuf }

impl RecordingStore {
    pub fn begin_session(&self, now: DateTime<Local>) -> Result<SessionPaths, RecordingError>;
    pub fn recoverable(&self) -> Result<Vec<RecoverableRecording>, RecordingError>;
    pub fn mark_complete(&self, session: &SessionPaths) -> Result<(), RecordingError>;
}
```

固定生成 `<data>/recordings/.incomplete/<uuid>`，拒绝包含父目录跳转的 session id。
最终路径先写同目录 `.tmp.m4a`，完成后 `rename` 为正式 `.m4a`。

在 `Cargo.toml` 增加直接依赖：

```toml
chrono = { version = "0.4", default-features = false, features = ["clock"] }
uuid = { version = "1", features = ["v4"] }

[dev-dependencies]
tempfile = "3"
```

- [ ] **Step 4: 实现 FFmpeg 混音**

双轨参数包含：

```text
-i system.caf -i microphone.caf
-filter_complex [0:a]aresample=48000:async=1:first_pts=0[sys];
                [1:a]aresample=48000:async=1:first_pts=0[mic];
                [sys][mic]amix=inputs=2:duration=first:dropout_transition=0,
                alimiter=limit=0.95[out]
-map [out] -ar 48000 -ac 1 -c:a aac -b:a 128k
```

实际参数数组不能通过 shell 字符串执行。麦克风缺失时直接把系统 CAF 转为相同规格。
FFmpeg 非零退出时返回 stderr 摘要，保留双轨和清单。成功后用 ffprobe 确认时长大于
零，再原子改名并删除该会话临时目录。

- [ ] **Step 5: 接入停止和恢复命令**

`stop_recording` 等待原生 `stopped`，切到 `mixing`，在阻塞线程执行混音，成功返回：

```rust
#[derive(Serialize)]
pub struct RecordingStopResult { pub final_path: String }
```

另加：

```rust
#[tauri::command]
pub fn list_recoverable_recordings(
    manager: State<'_, RecordingManager>,
) -> Result<Vec<RecoverableRecording>, String>;

#[tauri::command]
pub async fn retry_recording_mix(
    session_id: String,
    app: AppHandle,
    manager: State<'_, RecordingManager>,
) -> Result<RecordingStopResult, String>;
```

- [ ] **Step 6: 运行 Rust 测试和真实短音轨集成测试**

Run: `cd desktop/src-tauri && cargo test`

Expected: PASS。

Run: `cd desktop/src-tauri && cargo test recording::mix::tests::mixes_generated_short_tracks -- --ignored --nocapture`

Expected: 包内或系统 FFmpeg 可用时 PASS，输出可被 ffprobe 读取且时长大于零。

- [ ] **Step 7: 提交混音与恢复**

```bash
git add desktop/src-tauri/src/recording desktop/src-tauri/src/lib.rs \
  desktop/src-tauri/Cargo.toml desktop/src-tauri/Cargo.lock
git commit -m "feat: 保存并恢复录音文件"
```

---

### Task 6: 注册权限说明、macOS 门槛和退出保护

**Files:**
- Modify: `desktop/src-tauri/tauri.conf.json`
- Create: `desktop/src-tauri/Info.plist`
- Modify: `desktop/src-tauri/src/lib.rs`
- Modify: `desktop/src-tauri/src/recording/mod.rs`

**Interfaces:**
- Consumes: Task 4–5 的 `RecordingManager`、状态快照和设置跳转。
- Produces: 完整 Tauri command 集、`recording://close-requested`、macOS 权限用途说明。

- [ ] **Step 1: 写退出判定失败测试**

```rust
#[test]
fn active_phases_block_window_close() {
    for phase in [RecordingPhase::Starting, RecordingPhase::Recording,
                  RecordingPhase::Stopping, RecordingPhase::Mixing] {
        assert!(should_block_close(&phase));
    }
    assert!(!should_block_close(&RecordingPhase::Idle));
    assert!(!should_block_close(&RecordingPhase::Ready));
}
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd desktop/src-tauri && cargo test active_phases_block_window_close`

Expected: FAIL，`should_block_close` 尚不存在。

- [ ] **Step 3: 配置 macOS 13 和用途说明**

在 `tauri.conf.json` 中明确配置：

```json
"macOS": {
  "minimumSystemVersion": "13.0",
  "infoPlist": "Info.plist"
}
```

创建 `desktop/src-tauri/Info.plist`，包含：

```text
NSScreenCaptureUsageDescription = whosaid 只录制系统声音，不保存屏幕画面。
NSMicrophoneUsageDescription = whosaid 使用麦克风录制你在会议中的发言。
```

不得依赖安装后手工修改 Info.plist。

- [ ] **Step 4: 注册命令并实现关闭确认**

注册：

```text
get_recording_state
get_recording_permissions
start_recording
stop_recording
open_recording_settings
list_recoverable_recordings
retry_recording_mix
close_after_recording
```

`WindowEvent::CloseRequested { api, .. }` 遇到 active phase 时调用
`api.prevent_close()` 并 emit `recording://close-requested`。`close_after_recording`
只在状态不再 active 后设置一次性 `allow_close` 并关闭窗口，避免再次被拦截。

- [ ] **Step 5: 运行 Rust 测试并检查生成配置**

Run: `cd desktop/src-tauri && cargo test`

Expected: PASS。

Run: `cd desktop && npm run tauri build -- --no-bundle`

Expected: Apple Silicon 编译成功。

- [ ] **Step 6: 提交权限与生命周期**

```bash
git add desktop/src-tauri/tauri.conf.json desktop/src-tauri/Info.plist desktop/src-tauri/src
git commit -m "feat: 接入录音权限和退出保护"
```

---

### Task 7: 实现前端录音状态和主面板

**Files:**
- Create: `desktop/src/lib/recording.ts`
- Create: `desktop/src/lib/recordingState.ts`
- Create: `desktop/src/lib/recordingState.test.ts`
- Create: `desktop/src/lib/RecordingPanel.svelte`
- Create: `desktop/src/lib/RecordingPanel.test.ts`
- Modify: `desktop/src/lib/Sidebar.svelte`
- Modify: `desktop/src/lib/Icon.svelte`

**Interfaces:**
- Consumes: Task 6 的 Tauri commands 和 `recording://state`。
- Produces: `RecordingController`、`RecordingUiState`、`RecordingPanel`、Sidebar 的 `onStartRecording`。

- [ ] **Step 1: 写前端状态失败测试**

```ts
it("keeps recording when the microphone becomes unavailable", () => {
  const next = reduceRecordingState(recordingState(), {
    phase: "recording", microphone: "unavailable", system_audio: "active"
  });
  expect(next.phase).toBe("recording");
  expect(next.warning).toContain("系统声音不会中断");
});

it("shows saving, mixing, then submitting", () => {
  expect(labelForPhase("stopping")).toBe("正在保存录音");
  expect(labelForPhase("mixing")).toBe("正在合成音轨");
  expect(labelForPhase("submitting")).toBe("已开始转写");
});
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd desktop && npm test -- recordingState.test.ts`

Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现前端命令和状态封装**

`recording.ts` 固定导出：

```ts
export type RecordingPhase =
  | "idle" | "requesting_permissions" | "starting" | "recording"
  | "stopping" | "mixing" | "ready" | "submitting" | "failed";

export interface RecordingSnapshot {
  phase: RecordingPhase;
  elapsed_seconds: number;
  system_audio: SourceStatus;
  microphone: SourceStatus;
  final_path: string | null;
  recoverable_paths: string[];
  error: string | null;
}

export const startRecording = () => invoke<RecordingSnapshot>("start_recording");
export const stopRecording = () => invoke<{final_path: string}>("stop_recording");
export const watchRecording = (fn: (s: RecordingSnapshot) => void) =>
  listen<RecordingSnapshot>("recording://state", e => fn(e.payload));
```

`recordingState.ts` 只归并状态和生成用户文案，不调用 Tauri 或 HTTP。

- [ ] **Step 4: 写录音面板交互测试**

```ts
it("renders both sources and stops with one action", async () => {
  const onStop = vi.fn();
  const snapshot = {
    phase: "recording", elapsed_seconds: 42,
    system_audio: "active", microphone: "active",
    final_path: null, recoverable_paths: [], error: null
  } as const;
  render(RecordingPanel, { snapshot, onStop });
  expect(screen.getByText("电脑声音")).toBeTruthy();
  expect(screen.getByText("麦克风")).toBeTruthy();
  await fireEvent.click(screen.getByRole("button", { name: "停止并开始转写" }));
  expect(onStop).toHaveBeenCalledOnce();
});
```

另测麦克风黄色警告、停止按钮防重复点击、失败时显示可恢复路径。

- [ ] **Step 5: 实现 RecordingPanel 和 Sidebar 入口**

Sidebar props 增加：

```ts
onStartRecording: () => void;
recordingActive?: boolean;
```

按钮放在拖放提示上方，录制期间显示红色圆点和时长，并禁用重复开始。主面板按已
确认草图显示时长、电脑声音、麦克风、降级提示和停止按钮。颜色必须复用
`tokens.css` 的现有 token，危险红使用 `--danger`。

- [ ] **Step 6: 运行前端测试和 Svelte 检查**

Run: `cd desktop && npm test -- recordingState.test.ts RecordingPanel.test.ts`

Expected: PASS。

Run: `cd desktop && npm run check`

Expected: 0 errors、0 warnings。

- [ ] **Step 7: 提交录音界面**

```bash
git add desktop/src/lib
git commit -m "feat: 添加直接录音界面"
```

---

### Task 8: 接入自动提交、权限引导和恢复入口

**Files:**
- Modify: `desktop/src/routes/+page.svelte`
- Create: `desktop/src/lib/recordingFlow.ts`
- Create: `desktop/src/lib/recordingFlow.test.ts`
- Modify: `desktop/src/lib/RecordingPanel.svelte`
- Modify: `desktop/src/lib/Sidebar.svelte`

**Interfaces:**
- Consumes: Task 5 的最终路径/恢复命令、Task 7 的控制器、现有 `api.submitJob(path)`。
- Produces: 停止后自动创建并选中任务、权限设置入口、遗留录音恢复、退出确认。

- [ ] **Step 1: 写自动提交失败测试**

```ts
it("submits the finalized file and selects the new job", async () => {
  const api = { submitJob: vi.fn().mockResolvedValue("job-recorded") };
  const result = await finalizeAndSubmit(
    async () => ({ final_path: "/recordings/2026-08-12_10-30-15.m4a" }),
    api
  );
  expect(api.submitJob).toHaveBeenCalledWith("/recordings/2026-08-12_10-30-15.m4a");
  expect(result).toEqual({ jobId: "job-recorded", audioPath: expect.any(String) });
});

it("keeps finalPath when submission fails", async () => {
  const stopOk = async () => ({ final_path: "/recordings/meeting.m4a" });
  const api = { submitJob: vi.fn().mockRejectedValue(new Error("offline")) };
  await expect(finalizeAndSubmit(stopOk, api)).rejects.toMatchObject({
    finalPath: "/recordings/meeting.m4a"
  });
});
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd desktop && npm test -- recordingFlow.test.ts`

Expected: FAIL，`finalizeAndSubmit` 尚不存在。

- [ ] **Step 3: 实现停止后自动提交**

`recordingFlow.ts` 把停止、最终路径和 `submitJob` 串起来，但不直接操作 Svelte 状态：

```ts
export async function finalizeAndSubmit(
  stop: () => Promise<{ final_path: string }>,
  api: { submitJob(audioPath: string): Promise<string> }
): Promise<{ jobId: string; audioPath: string }>;
```

`+page.svelte` 成功后创建与拖入文件相同的 `JobSummary`，插入列表顶部、选中新任务、
切换到 transcript 并调用 `subscribe(job)`。失败时保留 `finalPath`，按钮文案为“重新提交转写”。

- [ ] **Step 4: 接入权限和恢复**

启动时调用 `list_recoverable_recordings`。有遗留会话时显示非阻塞提示：

```text
发现一段未完成录音（开始于 10:30），可尝试恢复。
[恢复录音]
```

系统录音权限未授权时，主面板说明“只录声音，不保存屏幕画面”，提供“打开系统设置”；
麦克风拒绝时不显示阻断弹窗。恢复混音成功后同样走 `submitJob`。

- [ ] **Step 5: 接入退出确认**

监听 `recording://close-requested`，显示两个动作：

- “停止并保存”：执行正常 stop；完成自动提交后调用 `close_after_recording`。
- “继续录音”：关闭确认框，不关闭窗口。

保存或混音失败时不关闭应用，先展示可恢复路径。

- [ ] **Step 6: 运行前端完整验证**

Run: `cd desktop && npm test`

Expected: 全部 PASS。

Run: `cd desktop && npm run check && npm run build`

Expected: 0 errors、0 warnings，构建成功。

- [ ] **Step 7: 提交完整流程**

```bash
git add desktop/src/routes/+page.svelte desktop/src/lib
git commit -m "feat: 录音结束后自动转写"
```

---

### Task 9: 文档、打包验收和真实腾讯会议验证

**Files:**
- Modify: `README.md`
- Modify: `desktop/README.md`
- Modify: `desktop/src-tauri/tauri.conf.json`（打包检查发现配置错误时修正）
- Modify: `desktop/src-tauri/Info.plist`（打包检查发现用途说明未合并时修正）

**Interfaces:**
- Consumes: Task 1–8 的完整录音功能。
- Produces: 可安装的本地 Apple Silicon 应用、完整自动测试证据和真机验收记录。

- [ ] **Step 1: 更新使用说明**

README 明确写：

- macOS 13+ Apple Silicon；
- 录全系统声音和可选麦克风；
- 首次需要系统录音权限，授权后可能要重启 whosaid；
- 麦克风不可用时继续录系统声；
- 不录屏幕画面；
- 停止后自动开始分人和转写；
- 临时恢复目录和失败时的处理方式。

- [ ] **Step 2: 跑完整自动验证**

Run: `cd desktop && npm run test:native`

Expected: PASS。

Run: `cd desktop/src-tauri && cargo test`

Expected: PASS。

Run: `cd desktop && npm test && npm run check && npm run build`

Expected: 前端全部 PASS，Svelte 0 errors、0 warnings，构建成功。

Run: `cd core && venv/bin/pytest -m "not slow"`

Expected: 现有后端非 slow 测试全部 PASS。

- [ ] **Step 3: 构建并检查 `.app`**

Run: `cd desktop && ./scripts/build-runtime.sh && npm run tauri build`

Expected: 生成 Apple Silicon `.app`；应用最低系统版本为 13.0；Info.plist 含两条用途说明；
二进制链接 ScreenCaptureKit 和 AVFoundation；包中不存在屏幕视频资源。

Run: `codesign --verify --deep --strict --verbose=2 desktop/src-tauri/target/release/bundle/macos/whosaid.app`

Expected: 验证通过。

- [ ] **Step 4: 做短录音真机冒烟**

在不打开腾讯会议时录制 30 秒，先播放系统音频并说话：

- 两路状态均为正在录制；
- 最终 `.m4a` 可播放且包含两边声音；
- 停止后自动创建任务；
- 应用启动前后 `codesign --verify --deep --strict` 均通过；
- `.app` 内没有新增 `__pycache__` 或其他运行时写入。

- [ ] **Step 5: 做腾讯会议和故障验收**

逐项记录结果：扬声器、耳机、麦克风不可用、拒绝两类权限、最小化、退出确认、
混音失败保轨、转写失败保最终文件。最后做两小时会议录制，使用 ffprobe 对比双轨/最终
时长，末端偏差必须不超过 250 ms。

- [ ] **Step 6: 更新本地应用并复验签名**

保留当前 `/Applications/whosaid.app` 的可恢复备份，再安装新 `.app`，执行：

```bash
codesign --force --deep --sign - /Applications/whosaid.app
codesign --verify --deep --strict --verbose=2 /Applications/whosaid.app
```

启动应用完成 30 秒录音，再次执行严格签名验证。只有两次都通过才宣布本地应用更新成功。

- [ ] **Step 7: 提交文档和最终修正**

```bash
git add README.md desktop/README.md desktop/src-tauri/tauri.conf.json desktop/src-tauri/Info.plist
git commit -m "docs: 补充 macOS 直接录音说明"
```

最后运行 `git status --short`，确认没有把历史未跟踪文档、模型、录音文件、运行时或
`.superpowers/` 内容加入提交。未经用户再次明确授权，不执行 `git push` 或 GitHub Release。
