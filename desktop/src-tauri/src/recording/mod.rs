pub mod native;
pub mod state;

use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use native::{platform_recorder, NativeRecorder};
use state::{
    NativeEvent, RecordingError, RecordingPhase, RecordingSnapshot, RecordingState,
    RecordingStopResult,
};
use tauri::{AppHandle, Emitter, Manager, State};

const STATE_EVENT: &str = "recording://state";
static SESSION_SEQUENCE: AtomicU64 = AtomicU64::new(0);

type StopOutcome = Result<RecordingStopResult, RecordingError>;

trait StateEventSink: Send + Sync {
    fn emit(&self, snapshot: RecordingSnapshot) -> Result<(), RecordingError>;
}

struct AppEventSink(AppHandle);

impl StateEventSink for AppEventSink {
    fn emit(&self, snapshot: RecordingSnapshot) -> Result<(), RecordingError> {
        self.0
            .emit(STATE_EVENT, snapshot)
            .map_err(|error| RecordingError::Event(error.to_string()))
    }
}

struct ActiveSession {
    generation: u64,
    stop_waiter: Option<mpsc::Sender<StopOutcome>>,
    protocol_error: Option<RecordingError>,
}

struct ManagerInner {
    state: RecordingState,
    active: Option<ActiveSession>,
}

pub struct RecordingManager {
    inner: Mutex<ManagerInner>,
    native: Arc<dyn NativeRecorder>,
    session_root: PathBuf,
    next_generation: AtomicU64,
}

impl RecordingManager {
    pub fn new(session_root: PathBuf) -> Self {
        Self::with_native(session_root, platform_recorder())
    }

    fn with_native(session_root: PathBuf, native: Arc<dyn NativeRecorder>) -> Self {
        Self {
            inner: Mutex::new(ManagerInner {
                state: RecordingState::new(),
                active: None,
            }),
            native,
            session_root,
            next_generation: AtomicU64::new(1),
        }
    }

    pub fn snapshot(&self) -> RecordingSnapshot {
        self.inner.lock().unwrap().state.snapshot()
    }

    fn start(&self, app: &AppHandle) -> Result<RecordingSnapshot, RecordingError> {
        let sink = AppEventSink(app.clone());
        let (generation, session_dir) = self.prepare_start(&sink)?;
        let (sender, receiver) = mpsc::channel();
        let event_app = app.clone();
        std::thread::spawn(move || {
            while let Ok(event) = receiver.recv() {
                let manager = event_app.state::<RecordingManager>();
                let sink = AppEventSink(event_app.clone());
                if let Err(error) = manager.handle_native_event(generation, &sink, event) {
                    eprintln!("[whosaid] 无法处理原生录音事件：{error}");
                }
            }
        });

        if let Err(error) = self.native.start(&session_dir, sender) {
            self.finish_start_failure(generation, &sink, error.clone())?;
            return Err(error);
        }
        Ok(self.snapshot())
    }

    fn prepare_start(&self, sink: &dyn StateEventSink) -> Result<(u64, PathBuf), RecordingError> {
        let generation = self.next_generation.fetch_add(1, Ordering::Relaxed);
        let snapshot = {
            let mut inner = self.inner.lock().unwrap();
            inner.state.begin_start()?;
            inner.active = Some(ActiveSession {
                generation,
                stop_waiter: None,
                protocol_error: None,
            });
            inner.state.snapshot()
        };
        if let Err(error) = sink.emit(snapshot) {
            self.finish_start_failure(generation, sink, error.clone())?;
            return Err(error);
        }

        if let Err(error) = self.native.permissions() {
            self.finish_start_failure(generation, sink, error.clone())?;
            return Err(error);
        }
        if let Err(error) = std::fs::create_dir_all(&self.session_root) {
            let error = RecordingError::Io(error.to_string());
            self.finish_start_failure(generation, sink, error.clone())?;
            return Err(error);
        }
        Ok((generation, self.next_session_dir()))
    }

    fn finish_start_failure(
        &self,
        generation: u64,
        sink: &dyn StateEventSink,
        error: RecordingError,
    ) -> Result<(), RecordingError> {
        let snapshot = {
            let mut inner = self.inner.lock().unwrap();
            if !Self::is_current(&inner, generation) {
                return Ok(());
            }
            inner.active = None;
            if inner.state.snapshot().phase != RecordingPhase::Failed {
                inner.state.apply(NativeEvent::FatalError {
                    message: error.to_string(),
                })?;
            }
            inner.state.snapshot()
        };
        sink.emit(snapshot)
    }

    fn stop(
        &self,
        sink: &dyn StateEventSink,
    ) -> Result<mpsc::Receiver<StopOutcome>, RecordingError> {
        let (receiver, snapshot) = {
            let mut inner = self.inner.lock().unwrap();
            inner.state.begin_stop()?;
            let (sender, receiver) = mpsc::channel();
            let active = inner.active.as_mut().ok_or(RecordingError::NotRecording)?;
            active.stop_waiter = Some(sender);
            (receiver, inner.state.snapshot())
        };

        let emit_result = sink.emit(snapshot);
        let stop_result = self.native.stop();
        if let Err(error) = stop_result {
            self.finish_synchronous_stop_failure(sink, error.clone(), emit_result.err());
            return Err(error);
        }
        emit_result?;
        Ok(receiver)
    }

    fn handle_native_event(
        &self,
        generation: u64,
        sink: &dyn StateEventSink,
        event: NativeEvent,
    ) -> Result<(), RecordingError> {
        if let NativeEvent::ProtocolError { message } = event {
            return self.handle_protocol_error(generation, sink, message);
        }

        let is_terminal = matches!(
            event,
            NativeEvent::Stopped { .. } | NativeEvent::FatalError { .. }
        );
        let (snapshot, waiter, outcome) = {
            let mut inner = self.inner.lock().unwrap();
            if !Self::is_current(&inner, generation) {
                return Ok(());
            }
            let protocol_error = inner
                .active
                .as_ref()
                .and_then(|active| active.protocol_error.clone());
            let native_outcome = match &event {
                NativeEvent::Stopped {
                    session_dir,
                    system_track,
                    microphone_track,
                } => Some(match protocol_error {
                    Some(error) => Err(error),
                    None => Ok(RecordingStopResult {
                        session_dir: session_dir.clone(),
                        system_track: system_track.clone(),
                        microphone_track: microphone_track.clone(),
                    }),
                }),
                NativeEvent::FatalError { message } => {
                    Some(Err(RecordingError::Native(message.clone())))
                }
                _ => None,
            };
            inner.state.apply(event)?;
            let snapshot = inner.state.snapshot();
            let waiter = if is_terminal {
                inner.active.take().and_then(|active| active.stop_waiter)
            } else {
                None
            };
            (snapshot, waiter, native_outcome)
        };

        let emit_result = sink.emit(snapshot);
        if let (Some(waiter), Some(outcome)) = (waiter, outcome) {
            let outcome = emit_result
                .as_ref()
                .map(|_| outcome)
                .unwrap_or_else(|error| Err(error.clone()));
            let _ = waiter.send(outcome);
        }
        emit_result
    }

    fn handle_protocol_error(
        &self,
        generation: u64,
        sink: &dyn StateEventSink,
        message: String,
    ) -> Result<(), RecordingError> {
        let protocol_error = RecordingError::InvalidNativeEvent(message.clone());
        let snapshot = {
            let mut inner = self.inner.lock().unwrap();
            if !Self::is_current(&inner, generation) {
                return Ok(());
            }
            inner.state.apply(NativeEvent::ProtocolError { message })?;
            if let Some(active) = inner.active.as_mut() {
                active.protocol_error = Some(protocol_error.clone());
            }
            inner.state.snapshot()
        };

        let emit_result = sink.emit(snapshot);
        // 协议已失去同步，必须主动停止，但仍保留 generation 和 waiter，等待真实原生终态。
        if let Err(stop_error) = self.native.stop() {
            self.finish_synchronous_stop_failure(sink, stop_error.clone(), emit_result.err());
            return Err(stop_error);
        }
        emit_result
    }

    fn finish_synchronous_stop_failure(
        &self,
        sink: &dyn StateEventSink,
        stop_error: RecordingError,
        emit_error: Option<RecordingError>,
    ) {
        let (waiter, snapshot) = {
            let mut inner = self.inner.lock().unwrap();
            let waiter = inner.active.take().and_then(|active| active.stop_waiter);
            if inner.state.snapshot().phase != RecordingPhase::Failed {
                let _ = inner.state.apply(NativeEvent::FatalError {
                    message: stop_error.to_string(),
                });
            }
            (waiter, inner.state.snapshot())
        };
        let final_emit_error = sink.emit(snapshot).err();
        if let Some(waiter) = waiter {
            let error = emit_error.or(final_emit_error).unwrap_or(stop_error);
            let _ = waiter.send(Err(error));
        }
    }

    fn is_current(inner: &ManagerInner, generation: u64) -> bool {
        inner
            .active
            .as_ref()
            .is_some_and(|active| active.generation == generation)
    }

    fn next_session_dir(&self) -> PathBuf {
        let timestamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis();
        let sequence = SESSION_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        self.session_root.join(format!("{timestamp}-{sequence}"))
    }
}

#[tauri::command]
pub fn start_recording(
    app: AppHandle,
    manager: State<'_, RecordingManager>,
) -> Result<RecordingSnapshot, String> {
    manager.start(&app).map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn stop_recording(
    app: AppHandle,
    manager: State<'_, RecordingManager>,
) -> Result<RecordingStopResult, String> {
    let receiver = manager
        .stop(&AppEventSink(app))
        .map_err(|error| error.to_string())?;
    let outcome = tauri::async_runtime::spawn_blocking(move || receiver.recv())
        .await
        .map_err(|error| error.to_string())?
        .map_err(|_| RecordingError::ChannelClosed.to_string())?;
    outcome.map_err(|error| error.to_string())
}

#[tauri::command]
pub fn get_recording_state(manager: State<'_, RecordingManager>) -> RecordingSnapshot {
    manager.snapshot()
}

#[cfg(test)]
mod tests {
    use std::path::Path;
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

    use super::*;
    use crate::recording::state::{PermissionSnapshot, PermissionStatus, SourceStatus};

    struct MockNative {
        stop_calls: AtomicUsize,
        stop_fails: AtomicBool,
    }

    impl MockNative {
        fn new() -> Self {
            Self {
                stop_calls: AtomicUsize::new(0),
                stop_fails: AtomicBool::new(false),
            }
        }
    }

    impl NativeRecorder for MockNative {
        fn permissions(&self) -> Result<PermissionSnapshot, RecordingError> {
            Ok(PermissionSnapshot {
                system_audio: PermissionStatus::Granted,
                microphone: PermissionStatus::Granted,
            })
        }

        fn start(
            &self,
            _session_dir: &Path,
            _sink: mpsc::Sender<NativeEvent>,
        ) -> Result<(), RecordingError> {
            Ok(())
        }

        fn stop(&self) -> Result<(), RecordingError> {
            self.stop_calls.fetch_add(1, Ordering::Relaxed);
            if self.stop_fails.load(Ordering::Relaxed) {
                Err(RecordingError::Native("同步停止失败".into()))
            } else {
                Ok(())
            }
        }

        fn open_settings(&self, _pane: state::SettingsPane) -> Result<(), RecordingError> {
            Ok(())
        }
    }

    #[derive(Default)]
    struct TestSink {
        snapshots: Mutex<Vec<RecordingSnapshot>>,
        fail: AtomicBool,
    }

    impl StateEventSink for TestSink {
        fn emit(&self, snapshot: RecordingSnapshot) -> Result<(), RecordingError> {
            if self.fail.load(Ordering::Relaxed) {
                return Err(RecordingError::Event("测试事件失败".into()));
            }
            self.snapshots.lock().unwrap().push(snapshot);
            Ok(())
        }
    }

    fn manager() -> (RecordingManager, Arc<MockNative>, TestSink) {
        let native = Arc::new(MockNative::new());
        let manager = RecordingManager::with_native(
            std::env::temp_dir().join("whosaid-recording-manager-tests"),
            native.clone(),
        );
        (manager, native, TestSink::default())
    }

    fn activate(manager: &RecordingManager, sink: &TestSink) -> u64 {
        let (generation, _) = manager.prepare_start(sink).unwrap();
        manager
            .handle_native_event(generation, sink, NativeEvent::Starting)
            .unwrap();
        manager
            .handle_native_event(generation, sink, NativeEvent::Recording { started_at: 1.0 })
            .unwrap();
        generation
    }

    #[test]
    fn stopped_wakes_waiter_with_raw_tracks() {
        let (manager, _, sink) = manager();
        let generation = activate(&manager, &sink);
        let receiver = manager.stop(&sink).unwrap();

        manager
            .handle_native_event(
                generation,
                &sink,
                NativeEvent::Stopped {
                    session_dir: "/session".into(),
                    system_track: "/session/system.caf".into(),
                    microphone_track: None,
                },
            )
            .unwrap();

        assert_eq!(
            receiver.recv().unwrap().unwrap(),
            RecordingStopResult {
                session_dir: "/session".into(),
                system_track: "/session/system.caf".into(),
                microphone_track: None,
            }
        );
    }

    #[test]
    fn native_fatal_wakes_stop_waiter() {
        let (manager, _, sink) = manager();
        let generation = activate(&manager, &sink);
        let receiver = manager.stop(&sink).unwrap();

        manager
            .handle_native_event(
                generation,
                &sink,
                NativeEvent::FatalError {
                    message: "系统中断".into(),
                },
            )
            .unwrap();

        assert_eq!(
            receiver.recv().unwrap().unwrap_err(),
            RecordingError::Native("系统中断".into())
        );
    }

    #[test]
    fn protocol_error_stops_native_but_waits_for_real_terminal_event() {
        let (manager, native, sink) = manager();
        let generation = activate(&manager, &sink);
        let receiver = manager.stop(&sink).unwrap();

        manager
            .handle_native_event(
                generation,
                &sink,
                NativeEvent::ProtocolError {
                    message: "坏 JSON".into(),
                },
            )
            .unwrap();
        assert_eq!(native.stop_calls.load(Ordering::Relaxed), 2);
        assert!(matches!(
            receiver.try_recv(),
            Err(mpsc::TryRecvError::Empty)
        ));

        manager
            .handle_native_event(
                generation,
                &sink,
                NativeEvent::Stopped {
                    session_dir: "/session".into(),
                    system_track: "/session/system.caf".into(),
                    microphone_track: None,
                },
            )
            .unwrap();
        assert_eq!(
            receiver.recv().unwrap().unwrap_err(),
            RecordingError::InvalidNativeEvent("坏 JSON".into())
        );
    }

    #[test]
    fn stale_generation_cannot_change_new_recording() {
        let (manager, _, sink) = manager();
        let old_generation = activate(&manager, &sink);
        manager
            .handle_native_event(
                old_generation,
                &sink,
                NativeEvent::FatalError {
                    message: "旧会话结束".into(),
                },
            )
            .unwrap();
        let new_generation = activate(&manager, &sink);
        let emitted_before_stale_event = sink.snapshots.lock().unwrap().len();

        manager
            .handle_native_event(
                old_generation,
                &sink,
                NativeEvent::Elapsed {
                    elapsed_seconds: 99,
                },
            )
            .unwrap();

        assert_ne!(old_generation, new_generation);
        assert_eq!(manager.snapshot().phase, RecordingPhase::Recording);
        assert_eq!(manager.snapshot().elapsed_seconds, 0);
        assert_eq!(
            sink.snapshots.lock().unwrap().len(),
            emitted_before_stale_event
        );
    }

    #[test]
    fn synchronous_start_failure_revokes_old_generation() {
        let (manager, _, sink) = manager();
        let (old_generation, _) = manager.prepare_start(&sink).unwrap();
        manager
            .finish_start_failure(
                old_generation,
                &sink,
                RecordingError::Native("同步启动失败".into()),
            )
            .unwrap();
        let new_generation = activate(&manager, &sink);

        manager
            .handle_native_event(
                old_generation,
                &sink,
                NativeEvent::FatalError {
                    message: "旧通道迟到".into(),
                },
            )
            .unwrap();

        assert_ne!(old_generation, new_generation);
        assert_eq!(manager.snapshot().phase, RecordingPhase::Recording);
        assert_eq!(manager.snapshot().error, None);
    }

    #[test]
    fn stale_terminal_cannot_wake_new_generation_waiter() {
        let (manager, _, sink) = manager();
        let old_generation = activate(&manager, &sink);
        manager
            .handle_native_event(
                old_generation,
                &sink,
                NativeEvent::FatalError {
                    message: "旧会话结束".into(),
                },
            )
            .unwrap();
        let new_generation = activate(&manager, &sink);
        let receiver = manager.stop(&sink).unwrap();

        manager
            .handle_native_event(
                old_generation,
                &sink,
                NativeEvent::Stopped {
                    session_dir: "/old".into(),
                    system_track: "/old/system.caf".into(),
                    microphone_track: None,
                },
            )
            .unwrap();
        assert!(matches!(
            receiver.try_recv(),
            Err(mpsc::TryRecvError::Empty)
        ));

        manager
            .handle_native_event(
                new_generation,
                &sink,
                NativeEvent::Stopped {
                    session_dir: "/new".into(),
                    system_track: "/new/system.caf".into(),
                    microphone_track: None,
                },
            )
            .unwrap();
        assert_eq!(receiver.recv().unwrap().unwrap().session_dir, "/new");
    }

    #[test]
    fn terminal_event_wakes_waiter_even_when_emit_fails() {
        let (manager, _, sink) = manager();
        let generation = activate(&manager, &sink);
        let receiver = manager.stop(&sink).unwrap();
        sink.fail.store(true, Ordering::Relaxed);

        assert!(matches!(
            manager.handle_native_event(
                generation,
                &sink,
                NativeEvent::Stopped {
                    session_dir: "/session".into(),
                    system_track: "/session/system.caf".into(),
                    microphone_track: None,
                },
            ),
            Err(RecordingError::Event(_))
        ));
        assert!(matches!(
            receiver.recv().unwrap(),
            Err(RecordingError::Event(_))
        ));
    }

    #[test]
    fn stopping_emit_failure_still_stops_and_accepts_terminal_event() {
        let (manager, native, sink) = manager();
        let generation = activate(&manager, &sink);
        sink.fail.store(true, Ordering::Relaxed);

        assert!(matches!(manager.stop(&sink), Err(RecordingError::Event(_))));
        assert_eq!(native.stop_calls.load(Ordering::Relaxed), 1);

        sink.fail.store(false, Ordering::Relaxed);
        manager
            .handle_native_event(
                generation,
                &sink,
                NativeEvent::FatalError {
                    message: "原生停止终态".into(),
                },
            )
            .unwrap();
        assert_eq!(manager.snapshot().phase, RecordingPhase::Failed);
        assert!(manager.prepare_start(&sink).is_ok());
    }

    #[test]
    fn protocol_stop_sync_failure_explicitly_terminates_session() {
        let (manager, native, sink) = manager();
        let generation = activate(&manager, &sink);
        let receiver = manager.stop(&sink).unwrap();
        native.stop_fails.store(true, Ordering::Relaxed);

        assert_eq!(
            manager
                .handle_native_event(
                    generation,
                    &sink,
                    NativeEvent::ProtocolError {
                        message: "坏 JSON".into(),
                    },
                )
                .unwrap_err(),
            RecordingError::Native("同步停止失败".into())
        );
        assert_eq!(
            receiver.recv().unwrap().unwrap_err(),
            RecordingError::Native("同步停止失败".into())
        );
        assert_eq!(manager.snapshot().phase, RecordingPhase::Failed);

        native.stop_fails.store(false, Ordering::Relaxed);
        assert!(manager.prepare_start(&sink).is_ok());
    }

    #[test]
    fn microphone_status_remains_nonfatal_at_manager_boundary() {
        let (manager, _, sink) = manager();
        let generation = activate(&manager, &sink);
        manager
            .handle_native_event(
                generation,
                &sink,
                NativeEvent::SourceStatus {
                    source: state::AudioSource::Microphone,
                    status: SourceStatus::Unavailable,
                },
            )
            .unwrap();
        assert_eq!(manager.snapshot().phase, RecordingPhase::Recording);
    }
}
