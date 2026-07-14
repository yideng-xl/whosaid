//! Tauri 外壳入口：启动时 spawn Python 转写服务，握手拿端口存入 app 状态，
//! 前端通过 `get_service_port` 命令拿端口后走 REST/WS 连本地服务；退出时 kill 子进程。
mod sidecar;

use std::sync::Mutex;

use tauri::Manager;

/// 持有 Python 子进程句柄，退出时 kill；用 Mutex<Option<..>> 便于 setup 后填入。
struct ServiceProcess(Mutex<Option<std::process::Child>>);

/// 已握手到的服务端口；None 表示尚未就绪，前端应轮询。
struct ServicePort(Mutex<Option<u16>>);

/// dev 阶段 core 仓库根（含 transcribe_core 包与 venv）：cargo run/tauri dev 的 cwd 为
/// desktop/src-tauri/，故 core 在 ../../core；可用 WHOSAID_CORE 覆盖。返回绝对路径。
fn core_root() -> std::path::PathBuf {
    if let Ok(p) = std::env::var("WHOSAID_CORE") {
        return std::path::PathBuf::from(p);
    }
    let mut p = std::env::current_dir().unwrap_or_default();
    p.push("../../core");
    // 规整掉 .. 段（失败则退回原路径），保证 PYTHONPATH 是绝对可用路径
    std::fs::canonicalize(&p).unwrap_or(p)
}

/// dev 阶段的 python 解释器；Task 9 再做 resolve_python 健壮化。
fn dev_python() -> String {
    std::env::var("WHOSAID_PYTHON").unwrap_or_else(|_| {
        core_root()
            .join("venv/bin/python")
            .to_string_lossy()
            .into_owned()
    })
}

/// 数据目录：~/Library/Application Support/whosaid（内核在此落 config.json 与持久化数据）。
fn data_dir() -> String {
    let mut base = std::env::var("HOME").unwrap_or_default();
    base.push_str("/Library/Application Support/whosaid");
    std::fs::create_dir_all(&base).ok();
    base
}

#[tauri::command]
fn get_service_port(state: tauri::State<'_, ServicePort>) -> Option<u16> {
    *state.0.lock().unwrap()
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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(ServiceProcess(Mutex::new(None)))
        .manage(ServicePort(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![
            get_service_port,
            pick_save_path,
            write_file
        ])
        .setup(|app| {
            let python = dev_python();
            let cwd = data_dir();
            // transcribe_core 未 pip 安装进 venv，只能从 core 根目录导入；
            // 故 cwd 用数据目录（config.json/持久化落此），PYTHONPATH 指向 core 根让 import 生效
            let pythonpath = core_root().to_string_lossy().into_owned();
            match sidecar::spawn_service(&python, &cwd, &pythonpath) {
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
            Ok(())
        })
        .on_window_event(|window, event| {
            // 窗口关闭时 kill 子进程，避免残留孤儿 python
            if let tauri::WindowEvent::Destroyed = event {
                if let Some(mut child) = window
                    .app_handle()
                    .state::<ServiceProcess>()
                    .0
                    .lock()
                    .unwrap()
                    .take()
                {
                    child.kill().ok();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
