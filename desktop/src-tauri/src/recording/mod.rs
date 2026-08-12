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

pub struct RecordingManager {
    state: Mutex<RecordingState>,
    native: Arc<dyn NativeRecorder>,
    session_root: PathBuf,
    stop_waiter: Mutex<Option<mpsc::Sender<StopOutcome>>>,
}

impl RecordingManager {
    pub fn new(session_root: PathBuf) -> Self {
        Self::with_native(session_root, platform_recorder())
    }

    fn with_native(session_root: PathBuf, native: Arc<dyn NativeRecorder>) -> Self {
        Self {
            state: Mutex::new(RecordingState::new()),
            native,
            session_root,
            stop_waiter: Mutex::new(None),
        }
    }

    pub fn snapshot(&self) -> RecordingSnapshot {
        self.state.lock().unwrap().snapshot()
    }

    fn start(&self, app: &AppHandle) -> Result<RecordingSnapshot, RecordingError> {
        let requesting_snapshot = {
            let mut state = self.state.lock().unwrap();
            state.begin_start()?;
            state.snapshot()
        };
        if let Err(error) = self.emit(app, &requesting_snapshot) {
            self.mark_failed(&error)?;
            return Err(error);
        }

        if let Err(error) = self.native.permissions() {
            self.fail(app, error.clone())?;
            return Err(error);
        }

        if let Err(error) = std::fs::create_dir_all(&self.session_root) {
            let error = RecordingError::Io(error.to_string());
            self.fail(app, error.clone())?;
            return Err(error);
        }
        let session_dir = self.next_session_dir();
        let (sender, receiver) = mpsc::channel();
        let event_app = app.clone();
        std::thread::spawn(move || {
            while let Ok(event) = receiver.recv() {
                let manager = event_app.state::<RecordingManager>();
                if let Err(error) = manager.handle_native_event(&event_app, event) {
                    eprintln!("[whosaid] 无法处理原生录音事件：{error}");
                }
            }
        });

        if let Err(error) = self.native.start(&session_dir, sender) {
            self.fail(app, error.clone())?;
            return Err(error);
        }
        Ok(self.snapshot())
    }

    fn stop(&self, app: &AppHandle) -> Result<mpsc::Receiver<StopOutcome>, RecordingError> {
        let stopping_snapshot = {
            let mut state = self.state.lock().unwrap();
            state.begin_stop()?;
            state.snapshot()
        };
        let (sender, receiver) = mpsc::channel();
        *self.stop_waiter.lock().unwrap() = Some(sender);

        if let Err(error) = self.emit(app, &stopping_snapshot) {
            // 状态已进入 stopping，仍继续请求原生停止，避免留下后台录音。
            let _ = self.native.stop();
            self.stop_waiter.lock().unwrap().take();
            return Err(error);
        }
        if let Err(error) = self.native.stop() {
            self.stop_waiter.lock().unwrap().take();
            self.fail(app, error.clone())?;
            return Err(error);
        }
        Ok(receiver)
    }

    fn handle_native_event(
        &self,
        app: &AppHandle,
        event: NativeEvent,
    ) -> Result<(), RecordingError> {
        let terminal_outcome = match &event {
            NativeEvent::Stopped {
                session_dir,
                system_track,
                microphone_track,
            } => Some(Ok(RecordingStopResult {
                session_dir: session_dir.clone(),
                system_track: system_track.clone(),
                microphone_track: microphone_track.clone(),
            })),
            NativeEvent::FatalError { message } => {
                Some(Err(RecordingError::Native(message.clone())))
            }
            _ => None,
        };

        let snapshot = {
            let mut state = self.state.lock().unwrap();
            state.apply(event)?;
            state.snapshot()
        };
        let emit_result = self.emit(app, &snapshot);

        if let Some(outcome) = terminal_outcome {
            if let Some(waiter) = self.stop_waiter.lock().unwrap().take() {
                let outcome = emit_result
                    .as_ref()
                    .map(|_| outcome)
                    .unwrap_or_else(|error| Err(error.clone()));
                let _ = waiter.send(outcome);
            }
        }
        emit_result
    }

    fn fail(
        &self,
        app: &AppHandle,
        error: RecordingError,
    ) -> Result<RecordingSnapshot, RecordingError> {
        let snapshot = self.mark_failed(&error)?;
        self.emit(app, &snapshot)?;
        Ok(snapshot)
    }

    fn mark_failed(&self, error: &RecordingError) -> Result<RecordingSnapshot, RecordingError> {
        let mut state = self.state.lock().unwrap();
        if state.snapshot().phase != RecordingPhase::Failed {
            state.apply(NativeEvent::FatalError {
                message: error.to_string(),
            })?;
        }
        Ok(state.snapshot())
    }

    fn emit(&self, app: &AppHandle, snapshot: &RecordingSnapshot) -> Result<(), RecordingError> {
        app.emit(STATE_EVENT, snapshot.clone())
            .map_err(|error| RecordingError::Event(error.to_string()))
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
    let receiver = manager.stop(&app).map_err(|error| error.to_string())?;
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
