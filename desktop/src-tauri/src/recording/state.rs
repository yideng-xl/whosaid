use std::fmt;

use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum RecordingPhase {
    Idle,
    RequestingPermissions,
    Starting,
    Recording,
    Stopping,
    Mixing,
    Ready,
    Failed,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum SourceStatus {
    Pending,
    Active,
    Unavailable,
    Denied,
    Interrupted,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum AudioSource {
    System,
    Microphone,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum SleepReason {
    DisplaySleep,
    SystemSleep,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum PowerEvent {
    DisplaySleep,
    DisplayWake,
    SystemSleep,
    SystemWake,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub enum PermissionStatus {
    Granted,
    NotDetermined,
    Denied,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct PermissionSnapshot {
    pub system_audio: PermissionStatus,
    pub microphone: PermissionStatus,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub enum SettingsPane {
    SystemAudio,
    Microphone,
}

impl SettingsPane {
    pub(crate) fn native_value(self) -> i32 {
        match self {
            Self::SystemAudio => 1,
            Self::Microphone => 2,
        }
    }
}

#[derive(Clone, Debug, Deserialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum NativeEvent {
    AudioLevel {
        source: AudioSource,
        peak: f32,
        #[serde(rename = "sampledAt")]
        sampled_at: f64,
    },
    Starting,
    Recording {
        #[serde(rename = "startedAt")]
        started_at: f64,
    },
    SourceStatus {
        source: AudioSource,
        status: SourceStatus,
    },
    Elapsed {
        #[serde(rename = "elapsedSeconds")]
        elapsed_seconds: u64,
    },
    Suspending {
        reason: SleepReason,
    },
    Stopped {
        #[serde(rename = "sessionDir")]
        session_dir: String,
        #[serde(rename = "systemTrack")]
        system_track: String,
        #[serde(rename = "microphoneTrack")]
        microphone_track: Option<String>,
    },
    FatalError {
        message: String,
    },
    #[serde(skip_deserializing)]
    ProtocolError {
        message: String,
    },
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct RecordingSnapshot {
    pub phase: RecordingPhase,
    pub elapsed_seconds: u64,
    pub system_audio: SourceStatus,
    pub microphone: SourceStatus,
    pub final_path: Option<String>,
    pub recoverable_paths: Vec<String>,
    pub error: Option<String>,
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct RecordingStopResult {
    pub session_dir: String,
    pub system_track: String,
    pub microphone_track: Option<String>,
}

#[derive(Clone, Debug, PartialEq)]
pub enum RecordingError {
    AlreadyRecording,
    NotRecording,
    InvalidTransition {
        phase: RecordingPhase,
        event: &'static str,
    },
    UnsupportedPlatform,
    Native(String),
    InvalidNativeEvent(String),
    Io(String),
    Event(String),
    ChannelClosed,
}

impl fmt::Display for RecordingError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::AlreadyRecording => write!(formatter, "已有录音正在进行"),
            Self::NotRecording => write!(formatter, "当前没有可停止的录音"),
            Self::InvalidTransition { phase, event } => {
                write!(formatter, "录音状态 {phase:?} 不能处理事件 {event}")
            }
            Self::UnsupportedPlatform => write!(formatter, "当前平台不支持直接录音"),
            Self::Native(message) => write!(formatter, "原生录音失败：{message}"),
            Self::InvalidNativeEvent(message) => {
                write!(formatter, "无法解析原生录音事件：{message}")
            }
            Self::Io(message) => write!(formatter, "无法准备录音目录：{message}"),
            Self::Event(message) => write!(formatter, "无法发送录音状态事件：{message}"),
            Self::ChannelClosed => write!(formatter, "原生录音事件通道已关闭"),
        }
    }
}

impl std::error::Error for RecordingError {}

#[derive(Clone, Debug)]
pub struct RecordingState {
    snapshot: RecordingSnapshot,
    started_at: Option<f64>,
}

enum StateTransition {
    BeginStart,
    Native(NativeEvent),
    BeginStop,
    BeginMixing,
    FinishMixing(String),
}

impl RecordingState {
    pub fn new() -> Self {
        Self {
            snapshot: RecordingSnapshot {
                phase: RecordingPhase::Idle,
                elapsed_seconds: 0,
                system_audio: SourceStatus::Pending,
                microphone: SourceStatus::Pending,
                final_path: None,
                recoverable_paths: Vec::new(),
                error: None,
            },
            started_at: None,
        }
    }

    pub fn snapshot(&self) -> RecordingSnapshot {
        self.snapshot.clone()
    }

    pub fn begin_start(&mut self) -> Result<(), RecordingError> {
        self.transition(StateTransition::BeginStart)
    }

    pub fn begin_stop(&mut self) -> Result<(), RecordingError> {
        self.transition(StateTransition::BeginStop)
    }

    pub fn begin_mixing(&mut self) -> Result<(), RecordingError> {
        self.transition(StateTransition::BeginMixing)
    }

    pub fn finish_mixing(&mut self, final_path: String) -> Result<(), RecordingError> {
        self.transition(StateTransition::FinishMixing(final_path))
    }

    pub fn set_recoverable_paths(&mut self, paths: Vec<String>) {
        self.snapshot.recoverable_paths = paths;
    }

    pub fn apply(&mut self, event: NativeEvent) -> Result<(), RecordingError> {
        self.transition(StateTransition::Native(event))
    }

    fn transition(&mut self, transition: StateTransition) -> Result<(), RecordingError> {
        match transition {
            StateTransition::BeginStart => match self.snapshot.phase {
                RecordingPhase::Idle | RecordingPhase::Ready | RecordingPhase::Failed => {
                    self.snapshot = RecordingSnapshot {
                        phase: RecordingPhase::RequestingPermissions,
                        elapsed_seconds: 0,
                        system_audio: SourceStatus::Pending,
                        microphone: SourceStatus::Pending,
                        final_path: None,
                        recoverable_paths: Vec::new(),
                        error: None,
                    };
                    self.started_at = None;
                    Ok(())
                }
                _ => Err(RecordingError::AlreadyRecording),
            },
            StateTransition::BeginStop => match self.snapshot.phase {
                RecordingPhase::Starting | RecordingPhase::Recording => {
                    self.snapshot.phase = RecordingPhase::Stopping;
                    Ok(())
                }
                _ => Err(RecordingError::NotRecording),
            },
            StateTransition::BeginMixing => {
                if self.snapshot.phase != RecordingPhase::Stopping {
                    return Err(self.invalid_transition("begin_mixing"));
                }
                self.snapshot.phase = RecordingPhase::Mixing;
                Ok(())
            }
            StateTransition::FinishMixing(final_path) => {
                if self.snapshot.phase != RecordingPhase::Mixing {
                    return Err(self.invalid_transition("finish_mixing"));
                }
                self.snapshot.phase = RecordingPhase::Ready;
                self.snapshot.final_path = Some(final_path);
                self.snapshot.recoverable_paths.clear();
                self.snapshot.error = None;
                Ok(())
            }
            StateTransition::Native(event) => self.apply_native(event),
        }
    }

    fn apply_native(&mut self, event: NativeEvent) -> Result<(), RecordingError> {
        match event {
            NativeEvent::AudioLevel { .. } => {} // 实时波形不改变录音状态。
            NativeEvent::Starting => {
                if self.snapshot.phase != RecordingPhase::RequestingPermissions {
                    return Err(self.invalid_transition("starting"));
                }
                self.snapshot.phase = RecordingPhase::Starting;
            }
            NativeEvent::Recording { started_at } => {
                if !matches!(
                    self.snapshot.phase,
                    RecordingPhase::Idle
                        | RecordingPhase::RequestingPermissions
                        | RecordingPhase::Starting
                ) {
                    return Err(self.invalid_transition("recording"));
                }
                self.snapshot.phase = RecordingPhase::Recording;
                self.snapshot.system_audio = SourceStatus::Active;
                self.started_at = Some(started_at);
            }
            NativeEvent::SourceStatus { source, status } => {
                if !matches!(
                    self.snapshot.phase,
                    RecordingPhase::Starting | RecordingPhase::Recording | RecordingPhase::Stopping
                ) {
                    return Err(self.invalid_transition("source_status"));
                }
                match source {
                    AudioSource::System => self.snapshot.system_audio = status,
                    AudioSource::Microphone => self.snapshot.microphone = status,
                }
            }
            NativeEvent::Elapsed { elapsed_seconds } => {
                if self.snapshot.phase != RecordingPhase::Recording {
                    return Err(self.invalid_transition("elapsed"));
                }
                self.snapshot.elapsed_seconds = elapsed_seconds;
            }
            NativeEvent::Suspending { .. } => {
                if !matches!(
                    self.snapshot.phase,
                    RecordingPhase::Starting | RecordingPhase::Recording
                ) {
                    return Err(self.invalid_transition("suspending"));
                }
                self.snapshot.phase = RecordingPhase::Stopping;
            }
            NativeEvent::Stopped {
                session_dir,
                system_track,
                microphone_track: _,
            } => {
                if !matches!(
                    self.snapshot.phase,
                    RecordingPhase::Stopping | RecordingPhase::Failed
                ) {
                    return Err(self.invalid_transition("stopped"));
                }
                if session_dir.trim().is_empty() || system_track.trim().is_empty() {
                    return Err(RecordingError::InvalidNativeEvent(
                        "stopped 的 sessionDir 和 systemTrack 不能为空".into(),
                    ));
                }
                // stopped 里的路径来自原生事件，状态机不能把它直接暴露给前端。
                // 只有 manager 对照受控 UUID 和落盘清单验证后才可写入恢复路径。
                self.snapshot.recoverable_paths.clear();
            }
            NativeEvent::FatalError { message } | NativeEvent::ProtocolError { message } => {
                if matches!(
                    self.snapshot.phase,
                    RecordingPhase::Idle | RecordingPhase::Ready
                ) {
                    return Err(self.invalid_transition("fatal_error"));
                }
                self.snapshot.phase = RecordingPhase::Failed;
                self.snapshot.system_audio = SourceStatus::Interrupted;
                self.snapshot.error = Some(message);
            }
        }
        Ok(())
    }

    fn invalid_transition(&self, event: &'static str) -> RecordingError {
        RecordingError::InvalidTransition {
            phase: self.snapshot.phase.clone(),
            event,
        }
    }
}

impl Default for RecordingState {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn microphone_failure_does_not_fail_recording() {
        let mut state = RecordingState::new();
        state
            .apply(NativeEvent::Recording { started_at: 10.0 })
            .unwrap();
        state
            .apply(NativeEvent::SourceStatus {
                source: AudioSource::Microphone,
                status: SourceStatus::Unavailable,
            })
            .unwrap();

        assert_eq!(state.snapshot().phase, RecordingPhase::Recording);
        assert_eq!(state.snapshot().microphone, SourceStatus::Unavailable);
    }

    #[test]
    fn duplicate_start_is_rejected() {
        let mut state = RecordingState::new();
        state.begin_start().unwrap();
        assert_eq!(
            state.begin_start().unwrap_err(),
            RecordingError::AlreadyRecording
        );
    }

    #[test]
    fn stop_from_idle_is_rejected() {
        let mut state = RecordingState::new();
        assert_eq!(
            state.begin_stop().unwrap_err(),
            RecordingError::NotRecording
        );
    }

    #[test]
    fn display_sleep_moves_active_recording_to_stopping() {
        let mut state = RecordingState::new();
        state
            .apply(NativeEvent::Recording { started_at: 10.0 })
            .unwrap();
        state
            .apply(NativeEvent::Suspending {
                reason: SleepReason::DisplaySleep,
            })
            .unwrap();

        assert_eq!(state.snapshot().phase, RecordingPhase::Stopping);
    }

    #[test]
    fn fatal_system_error_fails_recording() {
        let mut state = RecordingState::new();
        state
            .apply(NativeEvent::Recording { started_at: 10.0 })
            .unwrap();
        state
            .apply(NativeEvent::FatalError {
                message: "系统声音中断".into(),
            })
            .unwrap();

        let snapshot = state.snapshot();
        assert_eq!(snapshot.phase, RecordingPhase::Failed);
        assert_eq!(snapshot.system_audio, SourceStatus::Interrupted);
        assert_eq!(snapshot.error.as_deref(), Some("系统声音中断"));
    }

    #[test]
    fn stopping_can_advance_through_mixing_to_ready() {
        let mut state = RecordingState::new();
        state
            .apply(NativeEvent::Recording { started_at: 10.0 })
            .unwrap();
        state.begin_stop().unwrap();
        assert_eq!(state.snapshot().phase, RecordingPhase::Stopping);
        state
            .apply(NativeEvent::Stopped {
                session_dir: "/tmp/session".into(),
                system_track: "/tmp/session/system.caf".into(),
                microphone_track: None,
            })
            .unwrap();
        assert!(state.snapshot().recoverable_paths.is_empty());

        state.begin_mixing().unwrap();
        assert_eq!(state.snapshot().phase, RecordingPhase::Mixing);

        state.finish_mixing("/tmp/final.m4a".into()).unwrap();
        let snapshot = state.snapshot();
        assert_eq!(snapshot.phase, RecordingPhase::Ready);
        assert_eq!(snapshot.final_path.as_deref(), Some("/tmp/final.m4a"));
        assert!(snapshot.recoverable_paths.is_empty());
    }

    #[test]
    fn snapshot_serialization_uses_the_public_snake_case_contract() {
        let snapshot = RecordingState::new().snapshot();
        assert_eq!(
            serde_json::to_value(snapshot).unwrap(),
            serde_json::json!({
                "phase": "idle",
                "elapsed_seconds": 0,
                "system_audio": "pending",
                "microphone": "pending",
                "final_path": null,
                "recoverable_paths": [],
                "error": null,
            })
        );
    }
}
