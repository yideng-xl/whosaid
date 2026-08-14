//! Tauri 外壳入口：启动时 spawn Python 转写服务，握手拿端口存入 app 状态，
//! 前端通过 `get_service_port` 命令拿端口后走 REST/WS 连本地服务；退出时 kill 子进程。
mod recording;
mod sidecar;

use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use tauri::{Emitter, Manager};

const CLOSE_REQUESTED_EVENT: &str = "recording://close-requested";

#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct AppCapabilities {
    direct_recording: bool,
}

fn direct_recording_supported(is_macos: bool, is_arm64: bool) -> bool {
    is_macos && is_arm64
}

fn app_capabilities() -> AppCapabilities {
    AppCapabilities {
        direct_recording: direct_recording_supported(
            cfg!(target_os = "macos"),
            cfg!(target_arch = "aarch64"),
        ),
    }
}

/// 持有 Python 子进程句柄，退出时 kill；用 Mutex<Option<..>> 便于 setup 后填入。
struct ServiceProcess(Mutex<Option<std::process::Child>>);

/// 已握手到的服务端口；None 表示尚未就绪，前端应轮询。
struct ServicePort(Mutex<Option<u16>>);

/// `close_after_recording` 发起的关闭只放行对应窗口的一次关闭事件。
#[derive(Default)]
struct CloseGuardState(Mutex<HashSet<String>>);

impl CloseGuardState {
    fn allow_once(&self, label: &str) {
        self.0.lock().unwrap().insert(label.to_owned());
    }

    fn consume_allowance(&self, label: &str) -> bool {
        self.0.lock().unwrap().remove(label)
    }

    fn revoke(&self, label: &str) {
        self.0.lock().unwrap().remove(label);
    }
}

/// 三态优先级的通用选择器：运行时 env 覆盖 > 候选路径（`exists` 判真才用）> dev 兜底。
/// 纯函数，不摸文件系统/环境变量，靠调用方注入 `exists` 判定，故可单测（见 path_tests）。
fn pick_path(
    env_override: Option<PathBuf>,
    candidate: Option<PathBuf>,
    dev_fallback: PathBuf,
    exists: impl Fn(&Path) -> bool,
) -> PathBuf {
    if let Some(p) = env_override {
        return p;
    }
    if let Some(c) = &candidate {
        if exists(c) {
            return c.clone();
        }
    }
    dev_fallback
}

/// core 根目录（含 transcribe_core 包）三态解析：
/// ① WHOSAID_CORE 环境变量（dev/CI/排障用）
/// ② 打包态：resource_dir/core（仅当其下确有 transcribe_core 包才采用，由 Task 2 的
///    build-runtime.sh 组装进 `.app/Contents/Resources/core/transcribe_core`）
/// ③ dev 相对路径：cargo run/tauri dev 的 cwd 为 desktop/src-tauri/，故 core 在 ../../core
/// 不再有编译期 option_env! 烘焙兜底——那只对烘焙时的那台机器有效，分发态由②替代。
fn core_root(resource_dir: Option<PathBuf>) -> PathBuf {
    let env_override = std::env::var("WHOSAID_CORE").ok().map(PathBuf::from);
    let candidate = resource_dir.map(|rd| rd.join("core"));
    let dev_fallback = {
        let mut p = std::env::current_dir().unwrap_or_default();
        p.push("../../core");
        // 规整掉 .. 段（失败则退回原路径），保证 PYTHONPATH 是绝对可用路径
        std::fs::canonicalize(&p).unwrap_or(p)
    };
    pick_path(env_override, candidate, dev_fallback, |p| {
        p.join("transcribe_core").is_dir()
    })
}

fn python_relative_path(windows: bool) -> PathBuf {
    if windows {
        PathBuf::from("python").join("python.exe")
    } else {
        PathBuf::from("python").join("bin").join("python3")
    }
}

fn dev_python_relative_path(windows: bool) -> PathBuf {
    if windows {
        PathBuf::from("venv").join("Scripts").join("python.exe")
    } else {
        PathBuf::from("venv").join("bin").join("python")
    }
}

fn ffmpeg_binary_names(windows: bool) -> (&'static str, &'static str) {
    if windows {
        ("ffmpeg.exe", "ffprobe.exe")
    } else {
        ("ffmpeg", "ffprobe")
    }
}

fn resolve_data_dir(app_data_dir: PathBuf, home_dir: Option<PathBuf>, macos: bool) -> PathBuf {
    // v0.1.0 已把 macOS 数据写到这个历史目录；继续沿用，避免升级后任务和配置
    // 看起来“消失”。Windows 首版没有历史包，直接使用 Tauri 标准应用数据目录。
    if macos {
        if let Some(home) = home_dir {
            return home
                .join("Library")
                .join("Application Support")
                .join("whosaid");
        }
    }
    app_data_dir
}

/// python 解释器三态解析，语义与 core_root 对称：
/// ① WHOSAID_PYTHON 环境变量
/// ② 打包态：macOS 为 resource_dir/python/bin/python3，Windows 为
///    resource_dir/python/python.exe
/// ③ dev 相对路径：macOS 为 venv/bin/python，Windows 为 venv/Scripts/python.exe
fn dev_python(resource_dir: Option<PathBuf>) -> String {
    let env_override = std::env::var("WHOSAID_PYTHON").ok().map(PathBuf::from);
    let candidate = resource_dir
        .clone()
        .map(|rd| rd.join(python_relative_path(cfg!(target_os = "windows"))));
    let dev_fallback =
        core_root(resource_dir).join(dev_python_relative_path(cfg!(target_os = "windows")));
    pick_path(env_override, candidate, dev_fallback, |p| p.is_file())
        .to_string_lossy()
        .into_owned()
}

/// 包内静态 ffmpeg 目录：仅打包态（resource_dir/ffmpeg 确实存在）才返回 Some，供 spawn 时
/// 前插进子进程 PATH，让 audio.py/mlx_backend.py 调的裸 ffmpeg/ffprobe 命中包内版本。
/// dev 态资源不存在 → None → 不动 PATH，走系统 brew ffmpeg。
fn ffmpeg_dir(resource_dir: Option<PathBuf>) -> Option<String> {
    let dir = resource_dir?.join("ffmpeg");
    let (ffmpeg, ffprobe) = ffmpeg_binary_names(cfg!(target_os = "windows"));
    if dir.join(ffmpeg).is_file() && dir.join(ffprobe).is_file() {
        Some(dir.to_string_lossy().into_owned())
    } else {
        None
    }
}

fn recording_ffmpeg_tools(resource_dir: Option<PathBuf>) -> recording::mix::FfmpegTools {
    let (ffmpeg_name, ffprobe_name) = ffmpeg_binary_names(cfg!(target_os = "windows"));
    match ffmpeg_dir(resource_dir) {
        Some(directory) => recording::mix::FfmpegTools::new(
            PathBuf::from(&directory).join(ffmpeg_name),
            PathBuf::from(directory).join(ffprobe_name),
        ),
        None => recording::mix::FfmpegTools::new(ffmpeg_name.into(), ffprobe_name.into()),
    }
}

#[tauri::command]
fn get_service_port(state: tauri::State<'_, ServicePort>) -> Option<u16> {
    *state.0.lock().unwrap()
}

/// 前端以 Rust 编译目标为准决定是否初始化平台专属功能，读取失败时应隐藏该功能。
#[tauri::command]
fn get_app_capabilities() -> AppCapabilities {
    app_capabilities()
}

/// 导出用：弹系统保存对话框，返回用户选择的路径（取消则 None）。
#[tauri::command]
async fn pick_save_path(app: tauri::AppHandle, default_name: String) -> Option<String> {
    use tauri_plugin_dialog::DialogExt;
    app.dialog()
        .file()
        .set_file_name(&default_name)
        .blocking_save_file()
        .map(|p| p.to_string())
}

/// 把内容写到指定路径（导出稿子落盘）。
#[tauri::command]
fn write_file(path: String, content: String) -> Result<(), String> {
    std::fs::write(&path, content).map_err(|e| e.to_string())
}

#[tauri::command]
fn close_after_recording(
    window: tauri::WebviewWindow,
    manager: tauri::State<'_, recording::RecordingManager>,
    close_guard: tauri::State<'_, CloseGuardState>,
) -> Result<(), String> {
    if manager.blocks_window_close() {
        return Err("录音仍在保存，暂时不能关闭窗口".into());
    }

    let label = window.label().to_owned();
    close_guard.allow_once(&label);
    if let Err(error) = window.close() {
        close_guard.revoke(&label);
        return Err(error.to_string());
    }
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(ServiceProcess(Mutex::new(None)))
        .manage(ServicePort(Mutex::new(None)))
        .manage(CloseGuardState::default())
        .invoke_handler(tauri::generate_handler![
            get_service_port,
            get_app_capabilities,
            pick_save_path,
            write_file,
            recording::get_recording_state,
            recording::get_recording_permissions,
            recording::start_recording,
            recording::stop_recording,
            recording::open_recording_settings,
            recording::list_recoverable_recordings,
            recording::list_pending_recording_previews,
            recording::acknowledge_recording_preview,
            recording::rename_pending_recording_preview,
            recording::retry_recording_mix,
            close_after_recording
        ])
        .setup(|app| {
            // 打包态资源目录（.app/Contents/Resources/）；tauri dev 下通常返回 Some 但其下不会有
            // python/core（Task 2 的构建脚本没跑过），resolve_* 里的 exists 判定会自然落回 dev 分支。
            let resource_dir = app.path().resource_dir().ok();
            let python = dev_python(resource_dir.clone());
            let app_data_dir = app.path().app_data_dir()?;
            let home_dir = std::env::var_os("HOME").map(PathBuf::from);
            let data_dir = resolve_data_dir(app_data_dir, home_dir, cfg!(target_os = "macos"));
            std::fs::create_dir_all(&data_dir)?;
            app.manage(recording::RecordingManager::new(
                data_dir.join("recordings"),
                recording_ffmpeg_tools(resource_dir.clone()),
            ));
            let cwd = data_dir.to_string_lossy().into_owned();
            // transcribe_core 未 pip 安装进 venv，只能从 core 根目录导入；
            // 故 cwd 用数据目录（config.json/持久化落此），PYTHONPATH 指向 core 根让 import 生效
            let pythonpath = core_root(resource_dir.clone())
                .to_string_lossy()
                .into_owned();
            let ffmpeg = ffmpeg_dir(resource_dir);
            match sidecar::spawn_service(&python, &cwd, &pythonpath, ffmpeg.as_deref()) {
                Ok((child, port)) => {
                    *app.state::<ServiceProcess>().0.lock().unwrap() = Some(child);
                    *app.state::<ServicePort>().0.lock().unwrap() = Some(port);
                }
                Err(e) => {
                    // 启动失败先打日志，前端会因 get_service_port 一直为 None 显示“服务启动中…”；
                    // Task 9 补 resolve_python 与错误事件推送。
                    eprintln!("[whosaid] 服务启动失败: {e}（python={python}）");
                }
            }

            // 真 macOS vibrancy：给主窗口套 NSVisualEffectView（Sidebar 材质），
            // 配合窗口 transparent + CSS 侧栏透明，让磨砂从侧栏透出。
            // 仅 macOS 编译；apply_vibrancy 失败（如系统版本过旧）不影响其余启动流程。
            #[cfg(target_os = "macos")]
            {
                use window_vibrancy::{apply_vibrancy, NSVisualEffectMaterial};
                if let Some(win) = app.get_webview_window("main") {
                    let _ = apply_vibrancy(&win, NSVisualEffectMaterial::Sidebar, None, None);
                }
            }

            Ok(())
        })
        .on_window_event(|window, event| {
            match event {
                tauri::WindowEvent::CloseRequested { api, .. } => {
                    let manager = window.app_handle().state::<recording::RecordingManager>();
                    let close_guard = window.app_handle().state::<CloseGuardState>();
                    if manager.blocks_window_close() {
                        // 状态必须优先于一次性放行标记：若放行与新录音并发，新录音仍不可被关闭。
                        close_guard.revoke(window.label());
                        api.prevent_close();
                        if let Err(error) = window.emit(CLOSE_REQUESTED_EVENT, ()) {
                            eprintln!("[whosaid] 无法发送录音退出确认事件：{error}");
                        }
                    } else {
                        // 普通关闭本就可以通过；这里只负责消费命令设置的一次性标记。
                        close_guard.consume_allowance(window.label());
                    }
                }
                tauri::WindowEvent::Destroyed => {
                    // 窗口关闭时 kill 子进程（正常关窗的快路径）
                    window
                        .app_handle()
                        .state::<CloseGuardState>()
                        .revoke(window.label());
                    kill_service(window.app_handle());
                }
                _ => {}
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| match event {
            tauri::RunEvent::ExitRequested { api, .. }
                if app_handle
                    .state::<recording::RecordingManager>()
                    .blocks_window_close() =>
            {
                api.prevent_exit();
                if let Err(error) = app_handle.emit(CLOSE_REQUESTED_EVENT, ()) {
                    eprintln!("[whosaid] 无法发送录音退出确认事件：{error}");
                }
            }
            tauri::RunEvent::Exit => {
                // 应用退出（含 Cmd+Q / 进程正常结束）时兜底再 kill 一次；
                // 强杀/崩溃场景由 Python 端父进程看门狗自我了断（server.py）
                kill_service(app_handle);
            }
            _ => {}
        });
}

/// kill 掉持有的 Python 子进程（take 出来，幂等；重复调用无害）。
fn kill_service(app: &tauri::AppHandle) {
    if let Some(mut child) = app.state::<ServiceProcess>().0.lock().unwrap().take() {
        child.kill().ok();
    }
}

#[cfg(test)]
mod path_tests {
    use super::*;
    use std::path::PathBuf;

    #[test]
    fn tauri_enables_local_asset_protocol_for_recording_preview() {
        let manifest = include_str!("../Cargo.toml");
        assert!(manifest.contains("protocol-asset"));
    }

    #[test]
    fn close_allowance_is_consumed_once_per_window() {
        let guard = CloseGuardState::default();
        guard.allow_once("main");

        assert!(guard.consume_allowance("main"));
        assert!(!guard.consume_allowance("main"));
        assert!(!guard.consume_allowance("secondary"));
    }

    #[test]
    fn close_allowance_can_be_revoked_when_recording_restarts() {
        let guard = CloseGuardState::default();
        guard.allow_once("main");
        guard.revoke("main");

        assert!(!guard.consume_allowance("main"));
    }

    #[test]
    fn env_override_wins_over_everything() {
        let got = pick_path(
            Some(PathBuf::from("/env/core")),
            Some(PathBuf::from("/resource/core")),
            PathBuf::from("/dev/core"),
            |_p| true,
        );
        assert_eq!(got, PathBuf::from("/env/core"));
    }

    #[test]
    fn candidate_wins_when_exists_and_no_env_override() {
        let got = pick_path(
            None,
            Some(PathBuf::from("/resource/core")),
            PathBuf::from("/dev/core"),
            |p| p == std::path::Path::new("/resource/core"),
        );
        assert_eq!(got, PathBuf::from("/resource/core"));
    }

    #[test]
    fn falls_back_to_dev_when_candidate_missing() {
        let got = pick_path(
            None,
            Some(PathBuf::from("/resource/core")),
            PathBuf::from("/dev/core"),
            |_p| false,
        );
        assert_eq!(got, PathBuf::from("/dev/core"));
    }

    #[test]
    fn falls_back_to_dev_when_no_candidate_at_all() {
        let got = pick_path(None, None, PathBuf::from("/dev/core"), |_p| true);
        assert_eq!(got, PathBuf::from("/dev/core"));
    }

    #[test]
    fn ffmpeg_dir_none_when_no_resource_dir() {
        assert_eq!(ffmpeg_dir(None), None);
    }

    #[test]
    fn packaged_python_path_is_platform_specific() {
        assert_eq!(
            python_relative_path(false),
            PathBuf::from("python/bin/python3")
        );
        assert_eq!(
            python_relative_path(true),
            PathBuf::from("python").join("python.exe")
        );
    }

    #[test]
    fn dev_python_path_is_platform_specific() {
        assert_eq!(
            dev_python_relative_path(false),
            PathBuf::from("venv/bin/python")
        );
        assert_eq!(
            dev_python_relative_path(true),
            PathBuf::from("venv").join("Scripts").join("python.exe")
        );
    }

    #[test]
    fn ffmpeg_names_are_platform_specific() {
        assert_eq!(ffmpeg_binary_names(false), ("ffmpeg", "ffprobe"));
        assert_eq!(ffmpeg_binary_names(true), ("ffmpeg.exe", "ffprobe.exe"));
    }

    #[test]
    fn macos_keeps_legacy_data_directory() {
        let got = resolve_data_dir(
            PathBuf::from("/new/com.yideng.whosaid"),
            Some(PathBuf::from("/Users/test")),
            true,
        );
        assert_eq!(
            got,
            PathBuf::from("/Users/test/Library/Application Support/whosaid")
        );
    }

    #[test]
    fn windows_uses_tauri_app_data_directory() {
        let got = resolve_data_dir(
            PathBuf::from(r"C:\Users\test\AppData\Roaming\com.yideng.whosaid"),
            None,
            false,
        );
        assert_eq!(
            got,
            PathBuf::from(r"C:\Users\test\AppData\Roaming\com.yideng.whosaid")
        );
    }

    #[test]
    fn direct_recording_requires_macos_on_arm64() {
        assert!(direct_recording_supported(true, true));
        assert!(!direct_recording_supported(true, false));
        assert!(!direct_recording_supported(false, true));
        assert!(!direct_recording_supported(false, false));
    }

    #[test]
    fn compiled_recording_capability_matches_target() {
        assert_eq!(
            app_capabilities().direct_recording,
            cfg!(target_os = "macos") && cfg!(target_arch = "aarch64")
        );
    }

    #[test]
    fn ffmpeg_dir_none_when_binaries_absent() {
        // 一个存在但其下没有 ffmpeg/ffprobe 的临时目录 → None
        let tmp = std::env::temp_dir().join("whosaid_ffmpeg_test_absent");
        std::fs::create_dir_all(&tmp).unwrap();
        assert_eq!(ffmpeg_dir(Some(tmp.clone())), None);
        std::fs::remove_dir_all(&tmp).ok();
    }

    #[test]
    fn recording_tools_fall_back_to_path_binaries() {
        let tools = recording_ffmpeg_tools(None);
        let (ffmpeg, ffprobe) = ffmpeg_binary_names(cfg!(target_os = "windows"));
        assert_eq!(tools.ffmpeg, PathBuf::from(ffmpeg));
        assert_eq!(tools.ffprobe, PathBuf::from(ffprobe));
    }

    #[test]
    fn recording_tools_use_packaged_binaries_together() {
        let resources = tempfile::tempdir().unwrap();
        let directory = resources.path().join("ffmpeg");
        std::fs::create_dir(&directory).unwrap();
        let (ffmpeg, ffprobe) = ffmpeg_binary_names(cfg!(target_os = "windows"));
        std::fs::write(directory.join(ffmpeg), b"").unwrap();
        std::fs::write(directory.join(ffprobe), b"").unwrap();

        let tools = recording_ffmpeg_tools(Some(resources.path().to_path_buf()));
        assert_eq!(tools.ffmpeg, directory.join(ffmpeg));
        assert_eq!(tools.ffprobe, directory.join(ffprobe));
    }
}
