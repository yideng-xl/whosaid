pub mod mix;
pub mod native;
pub mod state;
pub mod storage;

use chrono::Local;
use serde::Serialize;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Mutex};

use mix::{mix_recording, probe_recording, FfmpegTools};
use native::{platform_recorder, NativeRecorder};
use state::{
    NativeEvent, PermissionSnapshot, PowerEvent, RecordingError, RecordingPhase, RecordingSnapshot,
    RecordingState, RecordingStopResult as StoppedTracks, SettingsPane, SleepReason,
};
use storage::{PendingRecordingPreview, RecordingStore, RecoverableRecording, RetryRecording};
use tauri::{AppHandle, Emitter, Manager, State};

const STATE_EVENT: &str = "recording://state";
type StopOutcome = Result<StoppedTracks, RecordingError>;

fn should_block_close(phase: &RecordingPhase, has_active_session: bool) -> bool {
    has_active_session
        || matches!(
            phase,
            RecordingPhase::RequestingPermissions
                | RecordingPhase::Starting
                | RecordingPhase::Recording
                | RecordingPhase::Stopping
                | RecordingPhase::Mixing
        )
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct RecordingStopResult {
    pub final_path: String,
}

trait StateEventSink: Send + Sync {
    fn emit(&self, snapshot: RecordingSnapshot) -> Result<(), RecordingError>;
    fn emit_level(&self, _level: AudioLevel) {}
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct AudioLevel {
    source: state::AudioSource,
    peak: f32,
    sampled_at: f64,
}

struct AppEventSink(AppHandle);

impl StateEventSink for AppEventSink {
    fn emit_level(&self, level: AudioLevel) {
        // 可视化发送失败不能中断录音或保存。
        let _ = self.0.emit("recording://level", level);
    }
    fn emit(&self, snapshot: RecordingSnapshot) -> Result<(), RecordingError> {
        crate::recording_overlay::on_recording_phase(&self.0, &snapshot.phase);
        self.0
            .emit(STATE_EVENT, snapshot)
            .map_err(|error| RecordingError::Event(error.to_string()))
    }
}

#[derive(Clone)]
struct ActiveSession {
    generation: u64,
    session_id: String,
    session_dir: PathBuf,
    stop_waiter: Option<mpsc::Sender<StopOutcome>>,
    protocol_error: Option<RecordingError>,
    automatic_stop: Option<SleepReason>,
}

#[derive(Clone)]
struct AutomaticSegment {
    tracks: StoppedTracks,
    reason: SleepReason,
}

#[derive(Default)]
struct PowerRecoveryState {
    cycle_active: bool,
    resume_allowed: bool,
    display_woke: bool,
    system_sleeping: bool,
    segment_saved: bool,
}

impl PowerRecoveryState {
    fn note_suspending(&mut self, reason: SleepReason) {
        self.cycle_active = true;
        self.segment_saved = false;
        self.display_woke = false;
        match reason {
            SleepReason::DisplaySleep => {
                self.resume_allowed = !self.system_sleeping;
            }
            SleepReason::SystemSleep => {
                self.system_sleeping = true;
                self.resume_allowed = false;
            }
        }
    }

    fn note_power_event(&mut self, event: PowerEvent) -> bool {
        match event {
            PowerEvent::DisplaySleep => {}
            PowerEvent::SystemSleep => {
                self.system_sleeping = true;
                self.resume_allowed = false;
            }
            PowerEvent::DisplayWake => {
                if self.cycle_active {
                    self.display_woke = true;
                }
            }
            PowerEvent::SystemWake => {
                self.system_sleeping = false;
                if self.cycle_active && !self.resume_allowed && self.segment_saved {
                    self.reset();
                }
            }
        }
        self.take_resume_if_ready()
    }

    fn note_segment_saved(&mut self) -> bool {
        self.segment_saved = true;
        let should_resume = self.take_resume_if_ready();
        if !should_resume && self.cycle_active && !self.resume_allowed && !self.system_sleeping {
            self.reset();
        }
        should_resume
    }

    fn take_resume_if_ready(&mut self) -> bool {
        if self.cycle_active
            && self.resume_allowed
            && self.display_woke
            && self.segment_saved
            && !self.system_sleeping
        {
            self.reset();
            true
        } else {
            false
        }
    }

    fn reset(&mut self) {
        *self = Self::default();
    }
}

struct ManagerInner {
    state: RecordingState,
    active: Option<ActiveSession>,
}

pub struct RecordingManager {
    inner: Mutex<ManagerInner>,
    native: Arc<dyn NativeRecorder>,
    store: RecordingStore,
    tools: FfmpegTools,
    coordination_lock: Mutex<()>,
    next_generation: AtomicU64,
    automatic_segments: Mutex<Vec<AutomaticSegment>>,
    power_recovery: Mutex<PowerRecoveryState>,
}

impl RecordingManager {
    pub fn new(recordings_root: PathBuf, tools: FfmpegTools) -> Self {
        Self::with_native_and_tools(recordings_root, platform_recorder(), tools)
    }

    #[cfg(test)]
    fn with_native(session_root: PathBuf, native: Arc<dyn NativeRecorder>) -> Self {
        Self::with_native_and_tools(
            session_root,
            native,
            FfmpegTools::new("ffmpeg".into(), "ffprobe".into()),
        )
    }

    fn with_native_and_tools(
        recordings_root: PathBuf,
        native: Arc<dyn NativeRecorder>,
        tools: FfmpegTools,
    ) -> Self {
        Self {
            inner: Mutex::new(ManagerInner {
                state: RecordingState::new(),
                active: None,
            }),
            native,
            store: RecordingStore::new(recordings_root),
            tools,
            coordination_lock: Mutex::new(()),
            next_generation: AtomicU64::new(1),
            automatic_segments: Mutex::new(Vec::new()),
            power_recovery: Mutex::new(PowerRecoveryState::default()),
        }
    }

    pub fn snapshot(&self) -> RecordingSnapshot {
        self.inner.lock().unwrap().state.snapshot()
    }

    pub(crate) fn blocks_window_close(&self) -> bool {
        let inner = self.inner.lock().unwrap();
        should_block_close(&inner.state.snapshot().phase, inner.active.is_some())
    }

    fn permissions(&self) -> Result<PermissionSnapshot, RecordingError> {
        self.native.permissions()
    }

    fn open_settings(&self, pane: SettingsPane) -> Result<(), RecordingError> {
        self.native.open_settings(pane)
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
                while let Some(segment) = manager.take_automatic_segment() {
                    let automatic_app = event_app.clone();
                    tauri::async_runtime::spawn(async move {
                        finalize_automatic_segment(automatic_app, segment).await;
                    });
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
        let _coordination = self.coordination_lock.lock().unwrap();
        let (generation, snapshot, session_dir) = {
            let mut inner = self.inner.lock().unwrap();
            // 原生 stop 是进程级 API；只要上一代 session 尚未收到真实终态或完成同步
            // 失败清理，就不能启动下一代，否则旧 stop 可能停掉新录音。
            if inner.active.is_some() {
                return Err(RecordingError::AlreadyRecording);
            }
            inner.state.begin_start()?;
            let session = match self.store.begin_session(Local::now()) {
                Ok(session) => session,
                Err(error) => {
                    let _ = inner.state.apply(NativeEvent::FatalError {
                        message: error.to_string(),
                    });
                    return Err(error);
                }
            };
            let generation = self.next_generation.fetch_add(1, Ordering::Relaxed);
            inner.active = Some(ActiveSession {
                generation,
                session_id: session.session_id,
                session_dir: session.session_dir.clone(),
                stop_waiter: None,
                protocol_error: None,
                automatic_stop: None,
            });
            (generation, inner.state.snapshot(), session.session_dir)
        };
        if let Err(error) = sink.emit(snapshot) {
            self.finish_start_failure(generation, sink, error.clone())?;
            return Err(error);
        }

        if let Err(error) = self.native.permissions() {
            self.finish_start_failure(generation, sink, error.clone())?;
            return Err(error);
        }
        Ok((generation, session_dir))
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
            if let Some(active) = inner.active.take() {
                // 权限/同步启动失败时只清理由 Rust 创建且仍为空的受控会话目录；
                // 如果原生已落下任何数据，保留给恢复扫描。
                let _ = self.store.discard_empty_session(&active.session_id);
            }
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
        let (expected_generation, receiver, snapshot) = {
            let mut inner = self.inner.lock().unwrap();
            let expected_generation = inner
                .active
                .as_ref()
                .map(|active| active.generation)
                .ok_or(RecordingError::NotRecording)?;
            inner.state.begin_stop()?;
            let (sender, receiver) = mpsc::channel();
            let active = inner.active.as_mut().expect("active 已在同一把锁内确认");
            active.stop_waiter = Some(sender);
            (expected_generation, receiver, inner.state.snapshot())
        };

        let emit_result = sink.emit(snapshot);
        let stop_result = self.request_native_stop(expected_generation);
        if let Err(error) = stop_result {
            self.finish_synchronous_stop_failure(
                expected_generation,
                sink,
                error.clone(),
                emit_result.err(),
            );
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
        if let NativeEvent::AudioLevel {
            source,
            peak,
            sampled_at,
        } = event
        {
            let inner = self.inner.lock().unwrap();
            if Self::is_current(&inner, generation)
                && inner.state.snapshot().phase == RecordingPhase::Recording
                && peak.is_finite()
                && (0.0..=1.0).contains(&peak)
                && sampled_at.is_finite()
            {
                sink.emit_level(AudioLevel {
                    source,
                    peak,
                    sampled_at,
                });
            }
            return Ok(());
        }
        if let NativeEvent::ProtocolError { message } = event {
            return self.handle_protocol_error(generation, sink, message);
        }
        if let NativeEvent::Suspending { reason } = event {
            let snapshot = {
                let mut inner = self.inner.lock().unwrap();
                if !Self::is_current(&inner, generation) {
                    return Ok(());
                }
                inner.state.apply(NativeEvent::Suspending { reason })?;
                if let Some(active) = inner.active.as_mut() {
                    active.automatic_stop = Some(reason);
                }
                self.power_recovery.lock().unwrap().note_suspending(reason);
                inner.state.snapshot()
            };
            return sink.emit(snapshot);
        }

        let is_terminal = matches!(
            event,
            NativeEvent::Stopped { .. } | NativeEvent::FatalError { .. }
        );
        let (snapshot, waiter, outcome, automatic_segment) = {
            let mut inner = self.inner.lock().unwrap();
            if !Self::is_current(&inner, generation) {
                return Ok(());
            }
            let protocol_error = inner
                .active
                .as_ref()
                .and_then(|active| active.protocol_error.clone());
            let terminal_session = is_terminal
                .then(|| inner.active.as_ref().cloned())
                .flatten();
            let automatic_reason = terminal_session
                .as_ref()
                .and_then(|active| active.automatic_stop);
            let native_outcome = match &event {
                NativeEvent::Stopped {
                    session_dir,
                    system_track,
                    microphone_track,
                } => Some(match protocol_error {
                    Some(error) => Err(error),
                    None => Ok(StoppedTracks {
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
            if let Some(active) = &terminal_session {
                inner
                    .state
                    .set_recoverable_paths(self.validate_recoverable_paths(active));
            }
            let snapshot = inner.state.snapshot();
            let waiter = if is_terminal {
                inner.active.take().and_then(|active| active.stop_waiter)
            } else {
                None
            };
            let automatic_segment = if waiter.is_none() {
                match (&native_outcome, automatic_reason) {
                    (Some(Ok(tracks)), Some(reason)) => Some(AutomaticSegment {
                        tracks: tracks.clone(),
                        reason,
                    }),
                    _ => None,
                }
            } else {
                None
            };
            (snapshot, waiter, native_outcome, automatic_segment)
        };

        let emit_result = sink.emit(snapshot);
        if let Some(segment) = automatic_segment {
            self.automatic_segments.lock().unwrap().push(segment);
        }
        if let (Some(waiter), Some(outcome)) = (waiter, outcome) {
            let outcome = emit_result
                .as_ref()
                .map(|_| outcome)
                .unwrap_or_else(|error| Err(error.clone()));
            let _ = waiter.send(outcome);
        }
        emit_result
    }

    pub(crate) fn watch_power_events(&self, app: &AppHandle) -> Result<(), RecordingError> {
        let (sender, receiver) = mpsc::channel();
        self.native.watch_power_events(sender)?;
        let power_app = app.clone();
        std::thread::spawn(move || {
            while let Ok(event) = receiver.recv() {
                let manager = power_app.state::<RecordingManager>();
                if manager
                    .power_recovery
                    .lock()
                    .unwrap()
                    .note_power_event(event)
                {
                    if let Err(error) = manager.start(&power_app) {
                        eprintln!("[whosaid] 屏幕恢复后无法开始下一段录音：{error}");
                    }
                }
            }
        });
        Ok(())
    }

    fn take_automatic_segment(&self) -> Option<AutomaticSegment> {
        let mut segments = self.automatic_segments.lock().unwrap();
        if segments.is_empty() {
            None
        } else {
            Some(segments.remove(0))
        }
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
        if let Err(stop_error) = self.request_native_stop(generation) {
            self.finish_synchronous_stop_failure(
                generation,
                sink,
                stop_error.clone(),
                emit_result.err(),
            );
            return Err(stop_error);
        }
        emit_result
    }

    fn request_native_stop(&self, expected_generation: u64) -> Result<(), RecordingError> {
        let inner = self.inner.lock().unwrap();
        if !Self::is_current(&inner, expected_generation) {
            // 真实终态已先一步处理完，无需再调用进程级 stop。
            return Ok(());
        }
        // 持有 session 锁到全局 stop 请求返回：原生回调只写 channel，不会重入本锁；
        // 这样终态处理和下一代 start 都不可能插进“核对 generation → 调 stop”的窗口。
        self.native.stop()
    }

    fn finish_synchronous_stop_failure(
        &self,
        expected_generation: u64,
        sink: &dyn StateEventSink,
        stop_error: RecordingError,
        emit_error: Option<RecordingError>,
    ) {
        let (waiter, snapshot) = {
            let mut inner = self.inner.lock().unwrap();
            if !Self::is_current(&inner, expected_generation) {
                return;
            }
            let active = inner.active.take();
            if let Some(active) = &active {
                inner
                    .state
                    .set_recoverable_paths(self.validate_recoverable_paths(active));
            }
            let waiter = active.and_then(|active| active.stop_waiter);
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

    fn validate_recoverable_paths(&self, active: &ActiveSession) -> Vec<String> {
        let Ok(recording) = self.store.recoverable_by_id(&active.session_id) else {
            return Vec::new();
        };
        if PathBuf::from(&recording.session_dir) != active.session_dir {
            return Vec::new();
        }
        std::iter::once(recording.session_dir)
            .chain(std::iter::once(recording.system_track))
            .chain(recording.microphone_track)
            .collect()
    }

    fn begin_mixing(&self, sink: &dyn StateEventSink) -> Result<RecordingSnapshot, RecordingError> {
        let snapshot = {
            let mut inner = self.inner.lock().unwrap();
            inner.state.begin_mixing()?;
            inner.state.snapshot()
        };
        if let Err(error) = sink.emit(snapshot.clone()) {
            eprintln!("[whosaid] 无法发送录音混音状态：{error}");
        }
        Ok(snapshot)
    }

    fn finish_mixing(
        &self,
        sink: &dyn StateEventSink,
        final_path: String,
    ) -> Result<RecordingSnapshot, RecordingError> {
        let snapshot = {
            let mut inner = self.inner.lock().unwrap();
            inner.state.finish_mixing(final_path)?;
            inner.state.snapshot()
        };
        if let Err(error) = sink.emit(snapshot.clone()) {
            eprintln!("[whosaid] 无法发送录音完成状态：{error}");
        }
        Ok(snapshot)
    }

    fn fail_mixing(&self, sink: &dyn StateEventSink, message: String) {
        let snapshot = {
            let mut inner = self.inner.lock().unwrap();
            let _ = inner.state.apply(NativeEvent::FatalError { message });
            inner.state.snapshot()
        };
        let _ = sink.emit(snapshot);
    }

    fn mix_tracks(&self, tracks: StoppedTracks) -> Result<RecordingStopResult, String> {
        let _guard = self
            .coordination_lock
            .lock()
            .map_err(|_| "录音协调锁已损坏".to_owned())?;
        let recording = self
            .store
            .recording_from_native_paths(
                &tracks.session_dir,
                &tracks.system_track,
                tracks.microphone_track.as_deref(),
            )
            .map_err(|error| error.to_string())?;
        self.mix_recording(&recording)
    }

    fn retry_mix(&self, session_id: &str) -> Result<RecordingStopResult, String> {
        let _guard = self
            .coordination_lock
            .lock()
            .map_err(|_| "录音协调锁已损坏".to_owned())?;
        if self
            .inner
            .lock()
            .unwrap()
            .active
            .as_ref()
            .is_some_and(|active| active.session_id == session_id)
        {
            return Err("该会话仍在录音，不能恢复混音".into());
        }
        match self
            .store
            .retry_recording(session_id)
            .map_err(|error| error.to_string())?
        {
            RetryRecording::Complete(final_path) => Ok(RecordingStopResult {
                final_path: {
                    probe_recording(&self.tools, &final_path)
                        .map_err(|error| format!("已完成录音校验失败：{error}"))?;
                    self.store
                        .reconcile_completed_session(session_id, &final_path)
                        .map_err(|error| format!("已完成录音对账失败：{error}"))?;
                    final_path.to_string_lossy().into_owned()
                },
            }),
            RetryRecording::Pending(recording) => self.mix_recording(&recording),
        }
    }

    fn list_recoverable(&self) -> Result<Vec<RecoverableRecording>, RecordingError> {
        let _guard = self.coordination_lock.lock().unwrap();
        let active_id = self
            .inner
            .lock()
            .unwrap()
            .active
            .as_ref()
            .map(|active| active.session_id.clone());
        let recordings = self.store.recoverable()?;
        let mut pending = Vec::new();
        for recording in recordings {
            if Some(&recording.session_id) == active_id.as_ref() {
                continue;
            }
            match self.store.completed_path(&recording.session_id) {
                Ok(Some(final_path)) => {
                    let reconciled = probe_recording(&self.tools, &final_path)
                        .map_err(|error| error.to_string())
                        .and_then(|_| {
                            self.store
                                .reconcile_completed_session(&recording.session_id, &final_path)
                                .map_err(|error| error.to_string())
                        });
                    if let Err(error) = reconciled {
                        eprintln!(
                            "[whosaid] 无法对账已完成录音会话 {}：{error}",
                            recording.session_id
                        );
                        pending.push(recording);
                    }
                }
                Ok(None) => pending.push(recording),
                Err(error) => {
                    eprintln!(
                        "[whosaid] 无法校验录音完成凭据 {}：{error}",
                        recording.session_id
                    );
                    pending.push(recording);
                }
            }
        }
        Ok(pending)
    }

    fn list_pending_previews(&self) -> Result<Vec<PendingRecordingPreview>, RecordingError> {
        let _guard = self.coordination_lock.lock().unwrap();
        self.store.pending_previews()
    }

    fn acknowledge_preview(&self, id: &str) -> Result<(), RecordingError> {
        let _guard = self.coordination_lock.lock().unwrap();
        self.store.acknowledge_preview(id)
    }

    fn delete_preview(&self, id: &str) -> Result<(), RecordingError> {
        let _guard = self.coordination_lock.lock().unwrap();
        self.store.delete_pending_preview(id)
    }

    fn rename_preview(
        &self,
        id: &str,
        name: &str,
    ) -> Result<PendingRecordingPreview, RecordingError> {
        let _guard = self.coordination_lock.lock().unwrap();
        self.store.rename_pending_preview(id, name)
    }

    fn mix_recording(
        &self,
        recording: &RecoverableRecording,
    ) -> Result<RecordingStopResult, String> {
        let final_path = mix_recording(&self.tools, &self.store, recording)
            .map_err(|error| format!("录音混音失败：{error}"))?;
        Ok(RecordingStopResult {
            final_path: final_path.to_string_lossy().into_owned(),
        })
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
    let sink = AppEventSink(app.clone());
    let receiver = manager.stop(&sink).map_err(|error| error.to_string())?;
    let tracks = tauri::async_runtime::spawn_blocking(move || receiver.recv())
        .await
        .map_err(|error| error.to_string())?
        .map_err(|_| RecordingError::ChannelClosed.to_string())?
        .map_err(|error| error.to_string())?;
    manager
        .begin_mixing(&sink)
        .map_err(|error| error.to_string())?;

    let mix_app = app.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        mix_app.state::<RecordingManager>().mix_tracks(tracks)
    })
    .await
    .map_err(|error| error.to_string())?;
    match result {
        Ok(result) => {
            manager
                .finish_mixing(&sink, result.final_path.clone())
                .map_err(|error| error.to_string())?;
            Ok(result)
        }
        Err(error) => {
            manager.fail_mixing(&sink, error.clone());
            Err(error)
        }
    }
}

async fn finalize_automatic_segment(app: AppHandle, segment: AutomaticSegment) {
    let AutomaticSegment {
        tracks,
        reason: _reason,
    } = segment;
    let sink = AppEventSink(app.clone());
    let manager = app.state::<RecordingManager>();
    if let Err(error) = manager.begin_mixing(&sink) {
        manager.fail_mixing(&sink, format!("休眠前保存录音失败：{error}"));
        return;
    }

    let mix_app = app.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        mix_app.state::<RecordingManager>().mix_tracks(tracks)
    })
    .await;
    let result = match result {
        Ok(result) => result,
        Err(error) => {
            manager.fail_mixing(&sink, format!("休眠前保存录音失败：{error}"));
            return;
        }
    };
    match result {
        Ok(result) => {
            if let Err(error) = manager.finish_mixing(&sink, result.final_path) {
                manager.fail_mixing(&sink, format!("休眠前保存录音失败：{error}"));
                return;
            }
            let should_resume = manager.power_recovery.lock().unwrap().note_segment_saved();
            if should_resume {
                if let Err(error) = manager.start(&app) {
                    eprintln!("[whosaid] 屏幕恢复后无法开始下一段录音：{error}");
                }
            }
        }
        Err(error) => manager.fail_mixing(&sink, error),
    }
}

#[tauri::command]
pub fn get_recording_state(manager: State<'_, RecordingManager>) -> RecordingSnapshot {
    manager.snapshot()
}

#[tauri::command]
pub fn get_recording_permissions(
    manager: State<'_, RecordingManager>,
) -> Result<PermissionSnapshot, String> {
    manager.permissions().map_err(|error| error.to_string())
}

#[tauri::command]
pub fn open_recording_settings(
    pane: SettingsPane,
    manager: State<'_, RecordingManager>,
) -> Result<(), String> {
    manager
        .open_settings(pane)
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn list_recoverable_recordings(
    manager: State<'_, RecordingManager>,
) -> Result<Vec<RecoverableRecording>, String> {
    manager
        .list_recoverable()
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn list_pending_recording_previews(
    manager: State<'_, RecordingManager>,
) -> Result<Vec<PendingRecordingPreview>, String> {
    manager
        .list_pending_previews()
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn acknowledge_recording_preview(
    id: String,
    manager: State<'_, RecordingManager>,
) -> Result<(), String> {
    manager
        .acknowledge_preview(&id)
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn delete_pending_recording_preview(
    id: String,
    manager: State<'_, RecordingManager>,
) -> Result<(), String> {
    manager
        .delete_preview(&id)
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn rename_pending_recording_preview(
    id: String,
    name: String,
    manager: State<'_, RecordingManager>,
) -> Result<PendingRecordingPreview, String> {
    manager
        .rename_preview(&id, &name)
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub async fn retry_recording_mix(
    session_id: String,
    app: AppHandle,
    _manager: State<'_, RecordingManager>,
) -> Result<RecordingStopResult, String> {
    tauri::async_runtime::spawn_blocking(move || {
        app.state::<RecordingManager>().retry_mix(&session_id)
    })
    .await
    .map_err(|error| error.to_string())?
}

#[cfg(test)]
mod tests {
    use std::path::Path;
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

    use super::*;
    use crate::recording::state::{PermissionSnapshot, PermissionStatus, SourceStatus};
    #[cfg(unix)]
    use std::os::unix::fs::PermissionsExt;
    #[cfg(unix)]
    use tempfile::TempDir;

    #[test]
    fn active_phases_block_window_close() {
        for phase in [
            RecordingPhase::RequestingPermissions,
            RecordingPhase::Starting,
            RecordingPhase::Recording,
            RecordingPhase::Stopping,
            RecordingPhase::Mixing,
        ] {
            assert!(should_block_close(&phase, false));
        }
        assert!(!should_block_close(&RecordingPhase::Idle, false));
        assert!(!should_block_close(&RecordingPhase::Ready, false));
        assert!(!should_block_close(&RecordingPhase::Failed, false));
        assert!(should_block_close(&RecordingPhase::Failed, true));
    }

    #[test]
    fn display_sleep_resumes_only_after_save_and_wake() {
        let mut recovery = PowerRecoveryState::default();
        recovery.note_suspending(SleepReason::DisplaySleep);

        assert!(!recovery.note_segment_saved());
        assert!(recovery.note_power_event(PowerEvent::DisplayWake));
        assert!(!recovery.cycle_active);
    }

    #[test]
    fn system_sleep_cancels_display_sleep_resume() {
        let mut recovery = PowerRecoveryState::default();
        recovery.note_suspending(SleepReason::DisplaySleep);

        assert!(!recovery.note_power_event(PowerEvent::SystemSleep));
        assert!(!recovery.note_segment_saved());
        assert!(!recovery.note_power_event(PowerEvent::SystemWake));
        assert!(!recovery.cycle_active);
    }

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
        levels: Mutex<Vec<AudioLevel>>,
        fail: AtomicBool,
    }

    impl StateEventSink for TestSink {
        fn emit_level(&self, level: AudioLevel) {
            self.levels.lock().unwrap().push(level);
        }
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
            std::env::temp_dir().join(format!(
                "whosaid-recording-manager-tests-{}",
                uuid::Uuid::new_v4()
            )),
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
    fn waveform_is_transient_and_ignores_stale_or_stopping_sessions() {
        let (manager, _, sink) = manager();
        let generation = activate(&manager, &sink);
        let before = sink.snapshots.lock().unwrap().len();
        let event = || NativeEvent::AudioLevel {
            source: state::AudioSource::Microphone,
            peak: 0.5,
            sampled_at: 1000.0,
        };
        manager
            .handle_native_event(generation + 1, &sink, event())
            .unwrap();
        assert!(sink.levels.lock().unwrap().is_empty());
        manager
            .handle_native_event(generation, &sink, event())
            .unwrap();
        assert_eq!(sink.levels.lock().unwrap().len(), 1);
        assert_eq!(sink.snapshots.lock().unwrap().len(), before);
        manager.inner.lock().unwrap().state.begin_stop().unwrap();
        manager
            .handle_native_event(generation, &sink, event())
            .unwrap();
        assert_eq!(sink.levels.lock().unwrap().len(), 1);
    }

    fn persist_active_manifest(manager: &RecordingManager) -> (String, PathBuf) {
        let active = manager
            .inner
            .lock()
            .unwrap()
            .active
            .as_ref()
            .unwrap()
            .clone();
        std::fs::write(active.session_dir.join("system.caf"), b"audio").unwrap();
        std::fs::write(
            active.session_dir.join("session.json"),
            serde_json::json!({
                "schemaVersion": 1,
                "sessionId": uuid::Uuid::new_v4().to_string(),
                "startedAt": 1786492215.0,
                "systemTrack": "system.caf",
                "microphoneTrack": null,
                "systemStatus": "interrupted",
                "microphoneStatus": "unavailable",
                "complete": false
            })
            .to_string(),
        )
        .unwrap();
        (active.session_id, active.session_dir)
    }

    #[cfg(unix)]
    fn receipt_crash_fixture() -> (TempDir, RecordingManager, String, PathBuf, PathBuf) {
        let root = tempfile::tempdir().unwrap();
        let recordings_root = root.path().join("recordings");
        let probe = root.path().join("ffprobe");
        std::fs::write(&probe, "#!/bin/sh\nprintf '1.0\\n'\n").unwrap();
        let mut permissions = std::fs::metadata(&probe).unwrap().permissions();
        permissions.set_mode(0o755);
        std::fs::set_permissions(&probe, permissions).unwrap();

        let native = Arc::new(MockNative::new());
        let manager = RecordingManager::with_native_and_tools(
            recordings_root.clone(),
            native,
            FfmpegTools::new("/usr/bin/false".into(), probe),
        );
        let session_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa".to_owned();
        let session_dir = recordings_root.join(".incomplete").join(&session_id);
        std::fs::create_dir_all(&session_dir).unwrap();
        std::fs::write(session_dir.join("system.caf"), b"source audio").unwrap();
        let manifest = serde_json::json!({
            "schemaVersion": 1,
            "sessionId": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            "startedAt": 1786492215.0,
            "systemTrack": "system.caf",
            "microphoneTrack": null,
            "systemStatus": "stopped",
            "microphoneStatus": "unavailable",
            "complete": false
        });
        std::fs::write(session_dir.join("session.json"), manifest.to_string()).unwrap();
        let recording = manager.store.recoverable_by_id(&session_id).unwrap();
        let final_path = recordings_root.join("finished.m4a");
        std::fs::write(&final_path, b"verified final audio").unwrap();
        manager
            .store
            .complete_with_receipt(&recording, &final_path)
            .unwrap();
        // 模拟 receipt 已持久化、manifest complete 尚未持久化时进程被强杀。
        std::fs::write(session_dir.join("session.json"), manifest.to_string()).unwrap();
        (root, manager, session_id, session_dir, final_path)
    }

    #[cfg(unix)]
    #[test]
    fn recoverable_scan_reconciles_receipt_crash_and_removes_session() {
        let (_root, manager, _session_id, session_dir, _final_path) = receipt_crash_fixture();

        assert!(manager.list_recoverable().unwrap().is_empty());
        assert!(!session_dir.exists());
    }

    #[cfg(unix)]
    #[test]
    fn retry_reconciles_receipt_crash_and_remains_idempotent() {
        let (_root, manager, session_id, session_dir, final_path) = receipt_crash_fixture();
        let canonical_final = std::fs::canonicalize(final_path)
            .unwrap()
            .to_string_lossy()
            .into_owned();

        assert_eq!(
            manager.retry_mix(&session_id).unwrap().final_path,
            canonical_final
        );
        assert!(!session_dir.exists());
        assert_eq!(
            manager.retry_mix(&session_id).unwrap().final_path,
            canonical_final
        );
    }

    #[cfg(unix)]
    #[test]
    fn receipt_reconciliation_failure_keeps_sources_and_session() {
        let (_root, manager, session_id, session_dir, _) = receipt_crash_fixture();
        let mut permissions = std::fs::metadata(&session_dir).unwrap().permissions();
        permissions.set_mode(0o555);
        std::fs::set_permissions(&session_dir, permissions).unwrap();

        assert_eq!(manager.list_recoverable().unwrap().len(), 1);
        assert!(manager.retry_mix(&session_id).is_err());
        assert!(session_dir.join("system.caf").is_file());
        assert!(session_dir.join("session.json").is_file());

        let mut permissions = std::fs::metadata(&session_dir).unwrap().permissions();
        permissions.set_mode(0o755);
        std::fs::set_permissions(&session_dir, permissions).unwrap();
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
            StoppedTracks {
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
    fn sleep_terminal_is_queued_for_automatic_save() {
        let (manager, _, sink) = manager();
        let generation = activate(&manager, &sink);
        let (_, session_dir) = persist_active_manifest(&manager);
        let system_track = session_dir.join("system.caf");

        manager
            .handle_native_event(
                generation,
                &sink,
                NativeEvent::Suspending {
                    reason: SleepReason::DisplaySleep,
                },
            )
            .unwrap();
        manager
            .handle_native_event(
                generation,
                &sink,
                NativeEvent::Stopped {
                    session_dir: session_dir.to_string_lossy().into_owned(),
                    system_track: system_track.to_string_lossy().into_owned(),
                    microphone_track: None,
                },
            )
            .unwrap();

        let queued = manager.take_automatic_segment().unwrap();
        assert_eq!(queued.reason, SleepReason::DisplaySleep);
        assert_eq!(queued.tracks.system_track, system_track.to_string_lossy());
        assert!(manager.inner.lock().unwrap().active.is_none());
        assert_eq!(manager.snapshot().phase, RecordingPhase::Stopping);
    }

    #[test]
    fn fatal_terminal_exposes_only_persisted_recoverable_session() {
        let (manager, _, sink) = manager();
        let generation = activate(&manager, &sink);
        let (session_id, session_dir) = persist_active_manifest(&manager);

        manager
            .handle_native_event(
                generation,
                &sink,
                NativeEvent::FatalError {
                    message: "系统声音中断".into(),
                },
            )
            .unwrap();

        let snapshot = manager.snapshot();
        assert_eq!(snapshot.phase, RecordingPhase::Failed);
        assert!(snapshot
            .recoverable_paths
            .contains(&session_dir.to_string_lossy().into_owned()));
        assert!(manager
            .list_recoverable()
            .unwrap()
            .iter()
            .any(|recording| recording.session_id == session_id));
    }

    #[test]
    fn protocol_error_stops_native_but_waits_for_real_terminal_event() {
        let (manager, native, sink) = manager();
        let generation = activate(&manager, &sink);
        let (_, session_dir) = persist_active_manifest(&manager);
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
        assert!(manager.snapshot().recoverable_paths.is_empty());
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
        assert!(manager
            .snapshot()
            .recoverable_paths
            .contains(&session_dir.to_string_lossy().into_owned()));
        assert_eq!(
            receiver.recv().unwrap().unwrap_err(),
            RecordingError::InvalidNativeEvent("坏 JSON".into())
        );
    }

    #[test]
    fn protocol_terminal_with_corrupt_manifest_exposes_no_raw_paths() {
        let (manager, _, sink) = manager();
        let generation = activate(&manager, &sink);
        let (_, session_dir) = persist_active_manifest(&manager);
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
        std::fs::write(session_dir.join("session.json"), "{broken").unwrap();

        manager
            .handle_native_event(
                generation,
                &sink,
                NativeEvent::Stopped {
                    session_dir: "/attacker".into(),
                    system_track: "/attacker/system.caf".into(),
                    microphone_track: Some("/attacker/microphone.caf".into()),
                },
            )
            .unwrap();

        assert!(manager.snapshot().recoverable_paths.is_empty());
        assert!(receiver.recv().unwrap().is_err());
    }

    #[test]
    fn protocol_failed_session_blocks_restart_until_real_terminal() {
        let (manager, _, sink) = manager();
        let generation = activate(&manager, &sink);

        manager
            .handle_native_event(
                generation,
                &sink,
                NativeEvent::ProtocolError {
                    message: "坏 JSON".into(),
                },
            )
            .unwrap();
        assert_eq!(manager.snapshot().phase, RecordingPhase::Failed);
        assert_eq!(
            manager.prepare_start(&sink).unwrap_err(),
            RecordingError::AlreadyRecording
        );

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
        assert!(manager.prepare_start(&sink).is_ok());
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
    fn old_stop_failure_cleanup_cannot_clear_new_generation() {
        let (manager, _, sink) = manager();
        let old_generation = activate(&manager, &sink);
        manager
            .handle_native_event(
                old_generation,
                &sink,
                NativeEvent::FatalError {
                    message: "旧会话终态".into(),
                },
            )
            .unwrap();
        let new_generation = activate(&manager, &sink);
        let receiver = manager.stop(&sink).unwrap();

        manager.finish_synchronous_stop_failure(
            old_generation,
            &sink,
            RecordingError::Native("旧 stop 迟到失败".into()),
            None,
        );

        assert!(RecordingManager::is_current(
            &manager.inner.lock().unwrap(),
            new_generation
        ));
        assert_eq!(manager.snapshot().phase, RecordingPhase::Stopping);
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
        let (_, session_dir) = persist_active_manifest(&manager);
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
        assert!(manager
            .snapshot()
            .recoverable_paths
            .contains(&session_dir.to_string_lossy().into_owned()));

        native.stop_fails.store(false, Ordering::Relaxed);
        assert!(manager.prepare_start(&sink).is_ok());
    }

    #[test]
    fn active_session_is_hidden_and_cannot_be_retried() {
        let (manager, _, sink) = manager();
        let (generation, _) = manager.prepare_start(&sink).unwrap();
        let (session_id, _) = persist_active_manifest(&manager);

        assert!(manager.store.recoverable_by_id(&session_id).is_ok());

        assert!(manager.list_recoverable().unwrap().is_empty());
        assert!(manager
            .retry_mix(&session_id)
            .unwrap_err()
            .contains("仍在录音"));
        assert!(RecordingManager::is_current(
            &manager.inner.lock().unwrap(),
            generation
        ));
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
