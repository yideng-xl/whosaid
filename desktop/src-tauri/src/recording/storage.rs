use std::fs;
use std::path::{Component, Path, PathBuf};

use chrono::{DateTime, Local, TimeZone};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use super::state::RecordingError;

const INCOMPLETE_DIRECTORY: &str = ".incomplete";
const MANIFEST_NAME: &str = "session.json";
const SYSTEM_TRACK_NAME: &str = "system.caf";
const MICROPHONE_TRACK_NAME: &str = "microphone.caf";

#[derive(Clone, Debug)]
pub struct RecordingStore {
    root: PathBuf,
}

#[derive(Clone, Debug)]
pub struct SessionPaths {
    pub session_id: String,
    pub session_dir: PathBuf,
    pub system_track: PathBuf,
    pub microphone_track: PathBuf,
    pub manifest: PathBuf,
    pub started_at: DateTime<Local>,
}

#[derive(Clone, Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct RecoverableRecording {
    pub session_id: String,
    pub started_at: f64,
    pub session_dir: String,
    pub system_track: String,
    pub microphone_track: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct SessionManifest {
    schema_version: u32,
    session_id: String,
    started_at: f64,
    system_track: String,
    microphone_track: Option<String>,
    system_status: String,
    microphone_status: String,
    complete: bool,
}

impl RecordingStore {
    pub fn new(root: PathBuf) -> Self {
        Self { root }
    }

    pub fn begin_session(&self, now: DateTime<Local>) -> Result<SessionPaths, RecordingError> {
        fs::create_dir_all(self.incomplete_root()).map_err(io_error)?;
        let session_id = Uuid::new_v4().to_string();
        let session_dir = self.incomplete_root().join(&session_id);
        fs::create_dir(&session_dir).map_err(io_error)?;
        Ok(SessionPaths {
            session_id,
            system_track: session_dir.join(SYSTEM_TRACK_NAME),
            microphone_track: session_dir.join(MICROPHONE_TRACK_NAME),
            manifest: session_dir.join(MANIFEST_NAME),
            session_dir,
            started_at: now,
        })
    }

    pub fn recoverable(&self) -> Result<Vec<RecoverableRecording>, RecordingError> {
        let root = self.incomplete_root();
        let entries = match fs::read_dir(&root) {
            Ok(entries) => entries,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
            Err(error) => return Err(io_error(error)),
        };

        let mut recordings = Vec::new();
        for entry in entries {
            let Ok(entry) = entry else { continue };
            let Ok(file_type) = entry.file_type() else {
                continue;
            };
            if !file_type.is_dir() || file_type.is_symlink() {
                continue;
            }
            let Some(session_id) = entry.file_name().to_str().map(str::to_owned) else {
                continue;
            };
            match self.load_recording(&session_id) {
                Ok(Some(recording)) => recordings.push(recording),
                Ok(None) | Err(_) => continue,
            }
        }
        recordings.sort_by(|left, right| {
            left.started_at
                .total_cmp(&right.started_at)
                .then_with(|| left.session_id.cmp(&right.session_id))
        });
        Ok(recordings)
    }

    pub fn recoverable_by_id(
        &self,
        session_id: &str,
    ) -> Result<RecoverableRecording, RecordingError> {
        validate_session_id(session_id)?;
        self.load_recording(session_id)?
            .ok_or_else(|| RecordingError::Io(format!("会话 {session_id} 已完成或不可恢复")))
    }

    pub fn recording_from_native_paths(
        &self,
        session_dir: &str,
        system_track: &str,
        microphone_track: Option<&str>,
    ) -> Result<RecoverableRecording, RecordingError> {
        let session_dir = Path::new(session_dir);
        let session_id = session_dir
            .file_name()
            .and_then(|value| value.to_str())
            .ok_or_else(|| RecordingError::Io("原生会话目录缺少安全的 UUID".into()))?;
        validate_session_id(session_id)?;
        let expected_dir = self.incomplete_root().join(session_id);
        if session_dir != expected_dir {
            return Err(RecordingError::Io("原生会话目录不在受控录音目录内".into()));
        }

        let recording = self.recoverable_by_id(session_id)?;
        if Path::new(system_track) != Path::new(&recording.system_track)
            || microphone_track.map(Path::new)
                != recording.microphone_track.as_deref().map(Path::new)
        {
            return Err(RecordingError::Io("原生音轨路径与会话清单不一致".into()));
        }
        Ok(recording)
    }

    pub fn final_path_at(&self, stem: &str, mut exists: impl FnMut(&Path) -> bool) -> PathBuf {
        let mut suffix = 1_u32;
        loop {
            let filename = if suffix == 1 {
                format!("{stem}.m4a")
            } else {
                format!("{stem}-{suffix}.m4a")
            };
            let candidate = self.root.join(filename);
            if !exists(&candidate) {
                return candidate;
            }
            suffix += 1;
        }
    }

    pub(crate) fn final_path_for(&self, recording: &RecoverableRecording) -> PathBuf {
        let seconds = recording.started_at.trunc() as i64;
        let stem = Local
            .timestamp_opt(seconds, 0)
            .single()
            .unwrap_or_else(Local::now)
            .format("%Y-%m-%d_%H-%M-%S")
            .to_string();
        self.final_path_at(&stem, Path::exists)
    }

    pub(crate) fn temporary_path_for(
        &self,
        recording: &RecoverableRecording,
        final_path: &Path,
    ) -> PathBuf {
        let stem = final_path
            .file_stem()
            .and_then(|value| value.to_str())
            .unwrap_or("recording");
        self.root
            .join(format!(".{stem}-{}.tmp.m4a", &recording.session_id[..8]))
    }

    pub(crate) fn ensure_root(&self) -> Result<(), RecordingError> {
        fs::create_dir_all(&self.root).map_err(io_error)
    }

    pub fn mark_complete(&self, session: &SessionPaths) -> Result<(), RecordingError> {
        validate_session_id(&session.session_id)?;
        let expected_dir = self.incomplete_root().join(&session.session_id);
        if session.session_dir != expected_dir
            || session.system_track != expected_dir.join(SYSTEM_TRACK_NAME)
            || session.microphone_track != expected_dir.join(MICROPHONE_TRACK_NAME)
            || session.manifest != expected_dir.join(MANIFEST_NAME)
        {
            return Err(RecordingError::Io("完成标记不属于受控录音目录".into()));
        }
        self.mark_manifest_complete(&session.manifest)
    }

    pub(crate) fn mark_recording_complete(
        &self,
        recording: &RecoverableRecording,
    ) -> Result<(), RecordingError> {
        let session = self.paths_for_recording(recording)?;
        self.mark_complete(&session)
    }

    pub(crate) fn remove_completed_session(
        &self,
        recording: &RecoverableRecording,
    ) -> Result<(), RecordingError> {
        let session = self.paths_for_recording(recording)?;
        fs::remove_dir_all(session.session_dir).map_err(io_error)
    }

    fn incomplete_root(&self) -> PathBuf {
        self.root.join(INCOMPLETE_DIRECTORY)
    }

    fn load_recording(
        &self,
        session_id: &str,
    ) -> Result<Option<RecoverableRecording>, RecordingError> {
        validate_session_id(session_id)?;
        let session_dir = self.incomplete_root().join(session_id);
        let metadata = fs::symlink_metadata(&session_dir).map_err(io_error)?;
        if !metadata.is_dir() || metadata.file_type().is_symlink() {
            return Err(RecordingError::Io("恢复会话目录不是普通目录".into()));
        }
        let manifest_path = session_dir.join(MANIFEST_NAME);
        let manifest_bytes = fs::read(&manifest_path).map_err(io_error)?;
        let manifest: SessionManifest = serde_json::from_slice(&manifest_bytes)
            .map_err(|error| RecordingError::Io(format!("无法读取会话清单：{error}")))?;
        if manifest.complete {
            return Ok(None);
        }
        // 原生桥保留自己的 sessionId；Rust 受控目录的 UUID 则作为恢复命令的 ID。
        // 两者各自必须合法，但原生协议并未约定它们相同。
        if manifest.schema_version != 1 || Uuid::parse_str(&manifest.session_id).is_err() {
            return Err(RecordingError::Io("会话清单标识无效".into()));
        }
        if !manifest.started_at.is_finite() || manifest.started_at <= 0.0 {
            return Err(RecordingError::Io("会话清单开始时间无效".into()));
        }
        validate_track_name(&manifest.system_track, SYSTEM_TRACK_NAME)?;
        if let Some(name) = &manifest.microphone_track {
            validate_track_name(name, MICROPHONE_TRACK_NAME)?;
        }

        let system_track = session_dir.join(&manifest.system_track);
        validate_regular_file(&system_track, "系统音轨")?;
        let microphone_track = manifest
            .microphone_track
            .as_ref()
            .map(|name| session_dir.join(name));
        if let Some(path) = &microphone_track {
            validate_regular_file(path, "麦克风音轨")?;
        }

        Ok(Some(RecoverableRecording {
            session_id: session_id.to_owned(),
            started_at: manifest.started_at,
            session_dir: path_string(&session_dir),
            system_track: path_string(&system_track),
            microphone_track: microphone_track.as_deref().map(path_string),
        }))
    }

    fn paths_for_recording(
        &self,
        recording: &RecoverableRecording,
    ) -> Result<SessionPaths, RecordingError> {
        validate_session_id(&recording.session_id)?;
        let expected_dir = self.incomplete_root().join(&recording.session_id);
        if Path::new(&recording.session_dir) != expected_dir {
            return Err(RecordingError::Io("恢复会话路径已离开受控目录".into()));
        }
        let seconds = recording.started_at.trunc() as i64;
        let started_at = Local
            .timestamp_opt(seconds, 0)
            .single()
            .ok_or_else(|| RecordingError::Io("恢复会话开始时间无效".into()))?;
        Ok(SessionPaths {
            session_id: recording.session_id.clone(),
            system_track: expected_dir.join(SYSTEM_TRACK_NAME),
            microphone_track: expected_dir.join(MICROPHONE_TRACK_NAME),
            manifest: expected_dir.join(MANIFEST_NAME),
            session_dir: expected_dir,
            started_at,
        })
    }

    fn mark_manifest_complete(&self, manifest_path: &Path) -> Result<(), RecordingError> {
        let bytes = fs::read(manifest_path).map_err(io_error)?;
        let mut manifest: SessionManifest = serde_json::from_slice(&bytes)
            .map_err(|error| RecordingError::Io(format!("无法读取会话清单：{error}")))?;
        manifest.complete = true;
        let encoded = serde_json::to_vec(&manifest)
            .map_err(|error| RecordingError::Io(format!("无法更新会话清单：{error}")))?;
        let temporary = manifest_path.with_extension("json.tmp");
        fs::write(&temporary, encoded).map_err(io_error)?;
        fs::rename(&temporary, manifest_path).map_err(io_error)
    }
}

fn validate_session_id(session_id: &str) -> Result<(), RecordingError> {
    let mut components = Path::new(session_id).components();
    if !matches!(components.next(), Some(Component::Normal(_)))
        || components.next().is_some()
        || Uuid::parse_str(session_id).is_err()
    {
        return Err(RecordingError::Io("录音会话 ID 无效".into()));
    }
    Ok(())
}

fn validate_track_name(actual: &str, expected: &str) -> Result<(), RecordingError> {
    if actual != expected || Path::new(actual).components().count() != 1 {
        return Err(RecordingError::Io("会话清单包含不受控的音轨路径".into()));
    }
    Ok(())
}

fn validate_regular_file(path: &Path, label: &str) -> Result<(), RecordingError> {
    let metadata = fs::symlink_metadata(path).map_err(io_error)?;
    if !metadata.is_file() || metadata.file_type().is_symlink() {
        return Err(RecordingError::Io(format!("{label}不是普通文件")));
    }
    Ok(())
}

fn path_string(path: &Path) -> String {
    path.to_string_lossy().into_owned()
}

fn io_error(error: std::io::Error) -> RecordingError {
    RecordingError::Io(error.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Local;
    use std::fs;
    use tempfile::tempdir;

    #[test]
    fn begin_session_stays_under_uuid_incomplete_directory() {
        let root = tempdir().unwrap();
        let store = RecordingStore::new(root.path().to_path_buf());

        let session = store.begin_session(Local::now()).unwrap();

        assert_eq!(
            session.session_dir.parent(),
            Some(root.path().join(".incomplete").as_path())
        );
        assert!(uuid::Uuid::parse_str(&session.session_id).is_ok());
        assert!(session.session_dir.is_dir());
    }

    #[test]
    fn final_name_is_collision_safe() {
        let root = tempdir().unwrap();
        let store = RecordingStore::new(root.path().to_path_buf());
        let first = store.final_path_at("2026-08-12_10-30-15", |_| false);
        let second = store.final_path_at("2026-08-12_10-30-15", |path| path == first);

        assert_ne!(first, second);
        assert_eq!(first.extension().unwrap(), "m4a");
    }

    #[test]
    fn retry_rejects_parent_directory_traversal() {
        let root = tempdir().unwrap();
        let store = RecordingStore::new(root.path().to_path_buf());
        assert!(store.recoverable_by_id("../outside").is_err());
    }

    #[test]
    fn manifest_rejects_parent_directory_track() {
        let root = tempdir().unwrap();
        let store = RecordingStore::new(root.path().to_path_buf());
        let session_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
        let session = root.path().join(".incomplete").join(session_id);
        fs::create_dir_all(&session).unwrap();
        fs::write(
            session.join("session.json"),
            serde_json::json!({
                "schemaVersion": 1,
                "sessionId": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                "startedAt": 1786492215.0,
                "systemTrack": "../outside.caf",
                "microphoneTrack": null,
                "systemStatus": "stopped",
                "microphoneStatus": "unavailable",
                "complete": false
            })
            .to_string(),
        )
        .unwrap();

        assert!(store.recoverable_by_id(session_id).is_err());
    }

    #[test]
    fn complete_marker_rejects_forged_manifest_path() {
        let root = tempdir().unwrap();
        let store = RecordingStore::new(root.path().to_path_buf());
        let mut session = store.begin_session(Local::now()).unwrap();
        session.manifest = root.path().join("outside.json");

        assert!(store.mark_complete(&session).is_err());
    }

    #[test]
    fn recovery_scan_ignores_complete_manifests() {
        let root = tempdir().unwrap();
        let store = RecordingStore::new(root.path().to_path_buf());
        let incomplete = root.path().join(".incomplete");
        fs::create_dir_all(&incomplete).unwrap();

        write_session(&incomplete, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", false);
        write_session(&incomplete, "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", true);

        let recovered = store.recoverable().unwrap();
        assert_eq!(recovered.len(), 1);
        assert_eq!(
            recovered[0].session_id,
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        );
    }

    #[test]
    fn native_manifest_id_may_differ_from_controlled_directory_id() {
        let root = tempdir().unwrap();
        let store = RecordingStore::new(root.path().to_path_buf());
        let directory_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
        let native_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
        let session = root.path().join(".incomplete").join(directory_id);
        fs::create_dir_all(&session).unwrap();
        fs::write(session.join("system.caf"), b"audio").unwrap();
        fs::write(
            session.join("session.json"),
            serde_json::json!({
                "schemaVersion": 1,
                "sessionId": native_id,
                "startedAt": 1786492215.0,
                "systemTrack": "system.caf",
                "microphoneTrack": null,
                "systemStatus": "stopped",
                "microphoneStatus": "unavailable",
                "complete": false
            })
            .to_string(),
        )
        .unwrap();

        let recording = store.recoverable_by_id(directory_id).unwrap();
        assert_eq!(recording.session_id, directory_id);
    }

    fn write_session(root: &std::path::Path, session_id: &str, complete: bool) {
        let session = root.join(session_id);
        fs::create_dir_all(&session).unwrap();
        fs::write(session.join("system.caf"), b"audio").unwrap();
        fs::write(
            session.join("session.json"),
            serde_json::json!({
                "schemaVersion": 1,
                "sessionId": session_id,
                "startedAt": 1786492215.0,
                "systemTrack": "system.caf",
                "microphoneTrack": null,
                "systemStatus": "stopped",
                "microphoneStatus": "unavailable",
                "complete": complete
            })
            .to_string(),
        )
        .unwrap();
    }
}
