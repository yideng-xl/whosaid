use std::sync::Mutex;
use tauri::{AppHandle, Emitter, Manager, WebviewUrl, WebviewWindowBuilder};

pub const LABEL: &str = "recording-overlay";
pub const EVENT: &str = "recording://overlay-enabled";

#[derive(Default)]
pub struct OverlayState(Mutex<OverlayConfig>);

#[derive(Default)]
struct OverlayConfig {
    enabled: bool,
    recording: bool,
}

impl OverlayConfig {
    fn visible(&self) -> bool {
        self.enabled && self.recording
    }
}

pub fn setup(app: &AppHandle) -> tauri::Result<()> {
    WebviewWindowBuilder::new(app, LABEL, WebviewUrl::App("recording-overlay".into()))
        .title("whosaid · 录音波形")
        .inner_size(230.0, 60.0)
        .position(24.0, 90.0)
        .resizable(false)
        .decorations(false)
        .transparent(true)
        .shadow(false)
        .always_on_top(true)
        .visible_on_all_workspaces(true)
        .skip_taskbar(true)
        .focused(false)
        .focusable(false)
        .accept_first_mouse(true)
        .visible(false)
        .build()?;
    Ok(())
}

// 所有显示操作排到主线程，并读取最新状态，避免快速开关/停止时旧操作重新显示窗口。
fn sync_visibility(app: &AppHandle) {
    let handle = app.clone();
    let _ = app.run_on_main_thread(move || {
        let visible = handle.state::<OverlayState>().0.lock().unwrap().visible();
        if let Some(window) = handle.get_webview_window(LABEL) {
            if window.is_visible().unwrap_or(false) != visible {
                let result = if visible {
                    window.show()
                } else {
                    window.hide()
                };
                if let Err(error) = result {
                    eprintln!("[whosaid] 无法更新波形悬浮窗：{error}");
                }
            }
        }
    });
}

pub fn on_recording_phase(app: &AppHandle, phase: &crate::recording::state::RecordingPhase) {
    app.state::<OverlayState>().0.lock().unwrap().recording =
        *phase == crate::recording::state::RecordingPhase::Recording;
    sync_visibility(app);
}

pub fn disable(app: &AppHandle) {
    app.state::<OverlayState>().0.lock().unwrap().enabled = false;
    sync_visibility(app);
    let _ = app.emit(EVENT, false);
}

#[tauri::command]
pub fn get_recording_overlay_enabled(app: AppHandle) -> bool {
    app.state::<OverlayState>().0.lock().unwrap().enabled
}

#[tauri::command]
pub async fn set_recording_overlay_enabled(app: AppHandle, enabled: bool) -> Result<bool, String> {
    if enabled && app.get_webview_window(LABEL).is_none() {
        return Err("波形悬浮窗暂不可用".into());
    }
    app.state::<OverlayState>().0.lock().unwrap().enabled = enabled;
    sync_visibility(&app);
    let _ = app.emit(EVENT, enabled);
    Ok(enabled)
}

#[tauri::command]
pub async fn open_recording_main_window(app: AppHandle) -> Result<(), String> {
    let main = app
        .get_webview_window("main")
        .ok_or("找不到 whosaid 主窗口")?;
    main.unminimize().map_err(|e| e.to_string())?;
    main.show().map_err(|e| e.to_string())?;
    main.set_focus().map_err(|e| e.to_string())?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn overlay_defaults_off_and_only_shows_while_recording() {
        let mut config = OverlayConfig::default();
        assert!(!config.enabled);
        config.recording = true;
        assert!(!config.visible());
        config.enabled = true;
        assert!(config.visible());
        config.recording = false;
        assert!(!config.visible());
        config.recording = true;
        assert!(config.visible());
        config.enabled = false;
        assert!(!config.visible());
    }
}
