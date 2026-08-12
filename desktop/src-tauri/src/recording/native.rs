use std::path::Path;
use std::sync::mpsc::Sender;

use super::state::{NativeEvent, PermissionSnapshot, RecordingError, SettingsPane};

pub trait NativeRecorder: Send + Sync {
    fn permissions(&self) -> Result<PermissionSnapshot, RecordingError>;
    fn start(&self, session_dir: &Path, sink: Sender<NativeEvent>) -> Result<(), RecordingError>;
    fn stop(&self) -> Result<(), RecordingError>;
    fn open_settings(&self, pane: SettingsPane) -> Result<(), RecordingError>;
}

pub fn platform_recorder() -> std::sync::Arc<dyn NativeRecorder> {
    std::sync::Arc::new(PlatformRecorder)
}

fn parse_native_event(json: &[u8]) -> Result<NativeEvent, RecordingError> {
    serde_json::from_slice(json)
        .map_err(|error| RecordingError::InvalidNativeEvent(error.to_string()))
}

fn parse_permission_snapshot(json: &[u8]) -> Result<PermissionSnapshot, RecordingError> {
    serde_json::from_slice(json)
        .map_err(|error| RecordingError::InvalidNativeEvent(error.to_string()))
}

#[cfg(target_os = "macos")]
mod platform {
    use std::ffi::{c_char, c_void, CStr, CString};
    use std::path::Path;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::mpsc::Sender;
    use std::sync::Arc;

    use super::{
        parse_native_event, parse_permission_snapshot, NativeEvent, NativeRecorder,
        PermissionSnapshot, RecordingError, SettingsPane,
    };

    const EXPECTED_API_VERSION: i32 = 1;

    extern "C" {
        fn whosaid_recorder_api_version() -> i32;
        fn whosaid_recorder_permission_snapshot() -> *mut c_char;
        fn whosaid_recorder_open_settings(pane: i32);
        fn whosaid_recorder_start(
            session_dir: *const c_char,
            callback: unsafe extern "C" fn(*const c_char, *mut c_void),
            context: *mut c_void,
        ) -> i32;
        fn whosaid_recorder_stop() -> i32;
        fn whosaid_recorder_free_string(value: *mut c_char);
    }

    struct CallbackContext {
        sink: Sender<NativeEvent>,
        native_owner_released: AtomicBool,
    }

    pub(super) struct PlatformRecorder;

    impl PlatformRecorder {
        fn ensure_api_version(&self) -> Result<(), RecordingError> {
            let version = unsafe { whosaid_recorder_api_version() };
            if version == EXPECTED_API_VERSION {
                Ok(())
            } else {
                Err(RecordingError::Native(format!(
                    "不兼容的录音接口版本 {version}，需要 {EXPECTED_API_VERSION}"
                )))
            }
        }
    }

    unsafe extern "C" fn recorder_callback(json: *const c_char, context: *mut c_void) {
        if context.is_null() {
            return;
        }

        let context_ptr = context.cast::<CallbackContext>();
        // C 侧持有一个 Arc 强引用。每次回调先临时增加引用，保证本次复制、解析和发送期间
        // 上下文始终有效；原生终态闭锁保证终态后不会再回调。
        unsafe { Arc::increment_strong_count(context_ptr) };
        let callback_context = unsafe { Arc::from_raw(context_ptr) };

        let (event, is_native_terminal) = if json.is_null() {
            (
                NativeEvent::FatalError {
                    message: "原生录音回调返回了空事件".into(),
                },
                false,
            )
        } else {
            // JSON 指针只在 C 回调期间有效，必须先完整复制进 Rust 自有内存再解析。
            let owned_json = unsafe { CStr::from_ptr(json) }.to_bytes().to_vec();
            match parse_native_event(&owned_json) {
                Ok(event) => {
                    let is_terminal = matches!(
                        event,
                        NativeEvent::Stopped { .. } | NativeEvent::FatalError { .. }
                    );
                    (event, is_terminal)
                }
                Err(error) => (
                    NativeEvent::FatalError {
                        message: error.to_string(),
                    },
                    false,
                ),
            }
        };
        let _ = callback_context.sink.send(event);

        if is_native_terminal
            && !callback_context
                .native_owner_released
                .swap(true, Ordering::AcqRel)
        {
            // 配对 start 中 Arc::into_raw 交给 C 侧持有的那一个强引用。
            unsafe { drop(Arc::from_raw(context_ptr)) };
        }
    }

    impl NativeRecorder for PlatformRecorder {
        fn permissions(&self) -> Result<PermissionSnapshot, RecordingError> {
            self.ensure_api_version()?;
            let snapshot = unsafe { whosaid_recorder_permission_snapshot() };
            if snapshot.is_null() {
                return Err(RecordingError::Native("无法读取录音权限".into()));
            }

            // 和事件回调同样先复制，再立即释放 C 分配的字符串。
            let owned_json = unsafe { CStr::from_ptr(snapshot) }.to_bytes().to_vec();
            unsafe { whosaid_recorder_free_string(snapshot) };
            parse_permission_snapshot(&owned_json)
        }

        fn start(
            &self,
            session_dir: &Path,
            sink: Sender<NativeEvent>,
        ) -> Result<(), RecordingError> {
            self.ensure_api_version()?;
            let session_dir = CString::new(session_dir.to_string_lossy().as_bytes())
                .map_err(|_| RecordingError::Native("录音目录包含空字符".into()))?;
            let callback_context = Arc::new(CallbackContext {
                sink,
                native_owner_released: AtomicBool::new(false),
            });
            let context_ptr = Arc::into_raw(Arc::clone(&callback_context));
            let result = unsafe {
                whosaid_recorder_start(
                    session_dir.as_ptr(),
                    recorder_callback,
                    context_ptr.cast_mut().cast(),
                )
            };
            if result == 0 {
                return Ok(());
            }

            // begin 失败若已发送 fatal_error，回调已经释放 C 侧引用；会话门禁拒绝等
            // 无回调失败则由这里回收，AtomicBool 避免双重释放。
            if !callback_context
                .native_owner_released
                .swap(true, Ordering::AcqRel)
            {
                unsafe { drop(Arc::from_raw(context_ptr)) };
            }
            Err(RecordingError::Native("无法启动原生录音".into()))
        }

        fn stop(&self) -> Result<(), RecordingError> {
            if unsafe { whosaid_recorder_stop() } == 0 {
                Ok(())
            } else {
                Err(RecordingError::Native("无法停止原生录音".into()))
            }
        }

        fn open_settings(&self, pane: SettingsPane) -> Result<(), RecordingError> {
            unsafe { whosaid_recorder_open_settings(pane.native_value()) };
            Ok(())
        }
    }

    #[cfg(test)]
    mod tests {
        use std::ffi::CString;
        use std::sync::atomic::AtomicBool;
        use std::sync::{mpsc, Arc};

        use super::*;

        #[test]
        fn callback_copies_json_before_returning_to_native() {
            let (sender, receiver) = mpsc::channel();
            let context = Arc::new(CallbackContext {
                sink: sender,
                native_owner_released: AtomicBool::new(false),
            });
            let context_ptr = Arc::into_raw(Arc::clone(&context));
            let json = CString::new(
                r#"{"type":"stopped","sessionDir":"/tmp/session","systemTrack":"/tmp/system.caf","microphoneTrack":null}"#,
            )
            .unwrap();

            unsafe { recorder_callback(json.as_ptr(), context_ptr.cast_mut().cast()) };
            drop(json);

            assert_eq!(
                receiver.recv().unwrap(),
                NativeEvent::Stopped {
                    session_dir: Some("/tmp/session".into()),
                    system_track: Some("/tmp/system.caf".into()),
                    microphone_track: None,
                }
            );
            assert!(context.native_owner_released.load(Ordering::Acquire));
        }

        #[test]
        fn malformed_nonterminal_event_does_not_release_native_context() {
            let (sender, receiver) = mpsc::channel();
            let context = Arc::new(CallbackContext {
                sink: sender,
                native_owner_released: AtomicBool::new(false),
            });
            let context_ptr = Arc::into_raw(Arc::clone(&context));
            let json = CString::new(r#"{"type":"elapsed","elapsedSeconds":"bad"}"#).unwrap();

            unsafe { recorder_callback(json.as_ptr(), context_ptr.cast_mut().cast()) };

            assert!(matches!(
                receiver.recv().unwrap(),
                NativeEvent::FatalError { .. }
            ));
            assert!(!context.native_owner_released.load(Ordering::Acquire));

            if !context.native_owner_released.swap(true, Ordering::AcqRel) {
                unsafe { drop(Arc::from_raw(context_ptr)) };
            }
        }
    }
}

#[cfg(not(target_os = "macos"))]
mod platform {
    use std::path::Path;
    use std::sync::mpsc::Sender;

    use super::{NativeEvent, NativeRecorder, PermissionSnapshot, RecordingError, SettingsPane};

    pub(super) struct PlatformRecorder;

    impl NativeRecorder for PlatformRecorder {
        fn permissions(&self) -> Result<PermissionSnapshot, RecordingError> {
            Err(RecordingError::UnsupportedPlatform)
        }

        fn start(
            &self,
            _session_dir: &Path,
            _sink: Sender<NativeEvent>,
        ) -> Result<(), RecordingError> {
            Err(RecordingError::UnsupportedPlatform)
        }

        fn stop(&self) -> Result<(), RecordingError> {
            Err(RecordingError::UnsupportedPlatform)
        }

        fn open_settings(&self, _pane: SettingsPane) -> Result<(), RecordingError> {
            Err(RecordingError::UnsupportedPlatform)
        }
    }
}

use platform::PlatformRecorder;

#[cfg(test)]
mod tests {
    use super::*;
    use crate::recording::state::{AudioSource, PermissionStatus, SourceStatus};

    #[test]
    fn parses_all_native_event_shapes() {
        assert_eq!(
            parse_native_event(br#"{"type":"starting"}"#).unwrap(),
            NativeEvent::Starting
        );
        assert_eq!(
            parse_native_event(br#"{"type":"recording","startedAt":10.5}"#).unwrap(),
            NativeEvent::Recording { started_at: 10.5 }
        );
        assert_eq!(
            parse_native_event(
                br#"{"type":"source_status","source":"microphone","status":"unavailable"}"#,
            )
            .unwrap(),
            NativeEvent::SourceStatus {
                source: AudioSource::Microphone,
                status: SourceStatus::Unavailable,
            }
        );
        assert_eq!(
            parse_native_event(br#"{"type":"elapsed","elapsedSeconds":42}"#).unwrap(),
            NativeEvent::Elapsed {
                elapsed_seconds: 42,
            }
        );
        assert_eq!(
            parse_native_event(
                br#"{"type":"stopped","sessionDir":null,"systemTrack":null,"microphoneTrack":null}"#,
            )
            .unwrap(),
            NativeEvent::Stopped {
                session_dir: None,
                system_track: None,
                microphone_track: None,
            }
        );
        assert_eq!(
            parse_native_event(br#"{"type":"fatal_error","message":"failed"}"#).unwrap(),
            NativeEvent::FatalError {
                message: "failed".into(),
            }
        );
    }

    #[test]
    fn parses_permission_snapshot() {
        assert_eq!(
            parse_permission_snapshot(
                br#"{"systemAudio":"notDetermined","microphone":"granted"}"#,
            )
            .unwrap(),
            PermissionSnapshot {
                system_audio: PermissionStatus::NotDetermined,
                microphone: PermissionStatus::Granted,
            }
        );
    }
}
