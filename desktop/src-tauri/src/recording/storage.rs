use std::fs;
use std::fs::OpenOptions;
use std::io::{Read, Write};
use std::path::{Component, Path, PathBuf};

use chrono::{DateTime, Local, TimeZone};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use uuid::Uuid;

use super::state::RecordingError;

const INCOMPLETE_DIRECTORY: &str = ".incomplete";
const MANIFEST_NAME: &str = "session.json";
const SYSTEM_TRACK_NAME: &str = "system.caf";
const MICROPHONE_TRACK_NAME: &str = "microphone.caf";
const COMPLETED_DIRECTORY: &str = ".completed";

#[derive(Clone, Debug)]
pub struct RecordingStore {
    root: PathBuf,
}

#[derive(Clone, Debug)]
pub struct SessionPaths {
    pub session_id: String,
    pub session_dir: PathBuf,
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

#[derive(Clone, Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct PendingRecordingPreview {
    pub id: String,
    pub final_path: String,
    pub created_at: f64,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) enum RetryRecording {
    Pending(RecoverableRecording),
    Complete(PathBuf),
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
enum MixStage {
    #[default]
    Pending,
    ReadyToInstall,
    Installed,
    Complete,
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
    #[serde(default)]
    final_path: Option<String>,
    #[serde(default)]
    mix_stage: MixStage,
    #[serde(default)]
    final_size: Option<u64>,
    #[serde(default)]
    final_sha256: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct CompletionReceipt {
    session_id: String,
    final_path: String,
    final_size: u64,
    final_sha256: String,
    #[serde(default)]
    created_at: f64,
}

impl RecordingStore {
    pub fn new(root: PathBuf) -> Self {
        Self { root }
    }

    pub fn begin_session(&self, _now: DateTime<Local>) -> Result<SessionPaths, RecordingError> {
        let (_, incomplete_root) = self.secure_roots(true)?;
        let session_id = Uuid::new_v4().to_string();
        let session_dir = incomplete_root.join(&session_id);
        fs::create_dir(&session_dir).map_err(io_error)?;
        validate_plain_directory(&session_dir, "录音会话目录")?;
        Ok(SessionPaths {
            session_id,
            session_dir,
        })
    }

    pub fn recoverable(&self) -> Result<Vec<RecoverableRecording>, RecordingError> {
        let (_, root) = match self.secure_roots(false) {
            Ok(roots) => roots,
            Err(RecordingError::Io(message)) if message.contains("不存在") => {
                return Ok(Vec::new())
            }
            Err(error) => return Err(error),
        };
        let entries = fs::read_dir(&root).map_err(io_error)?;

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
            match self.load_recording(&session_id, false) {
                Ok(Some(recording)) => recordings.push(recording),
                Ok(None) => continue,
                Err(error) => {
                    eprintln!("[whosaid] 忽略损坏的恢复会话 {session_id}：{error}");
                }
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
        self.load_recording(session_id, false)?
            .ok_or_else(|| RecordingError::Io(format!("会话 {session_id} 已完成或不可恢复")))
    }

    pub(crate) fn retry_recording(
        &self,
        session_id: &str,
    ) -> Result<RetryRecording, RecordingError> {
        validate_session_id(session_id)?;
        if let Some(path) = self.load_receipt(session_id)? {
            return Ok(RetryRecording::Complete(path));
        }
        match self.load_recording(session_id, true)? {
            Some(recording) => Ok(RetryRecording::Pending(recording)),
            None => Err(RecordingError::Io(format!(
                "会话 {session_id} 已完成但缺少有效完成凭据"
            ))),
        }
    }

    pub(crate) fn completed_path(
        &self,
        session_id: &str,
    ) -> Result<Option<PathBuf>, RecordingError> {
        validate_session_id(session_id)?;
        self.load_receipt(session_id)
    }

    pub fn pending_previews(&self) -> Result<Vec<PendingRecordingPreview>, RecordingError> {
        let completed = match self.secure_completed_root(false) {
            Ok(path) => path,
            Err(RecordingError::Io(message)) if message.contains("不存在") => {
                return Ok(Vec::new())
            }
            Err(error) => return Err(error),
        };
        let mut previews = Vec::new();
        for entry in fs::read_dir(&completed).map_err(io_error)? {
            let Ok(entry) = entry else { continue };
            let Ok(file_type) = entry.file_type() else {
                continue;
            };
            if !file_type.is_file() || file_type.is_symlink() {
                continue;
            }
            let Some(filename) = entry.file_name().to_str().map(str::to_owned) else {
                continue;
            };
            let Some(session_id) = filename.strip_suffix(".json") else {
                continue;
            };
            if validate_session_id(session_id).is_err() {
                continue;
            }
            match self.read_receipt(session_id) {
                Ok(Some((receipt, receipt_path))) => {
                    let created_at = if receipt.created_at.is_finite() && receipt.created_at > 0.0 {
                        receipt.created_at
                    } else {
                        receipt_path
                            .metadata()
                            .ok()
                            .and_then(|metadata| metadata.modified().ok())
                            .and_then(|time| time.duration_since(std::time::UNIX_EPOCH).ok())
                            .map(|duration| duration.as_secs_f64())
                            .unwrap_or(1.0)
                    };
                    previews.push(PendingRecordingPreview {
                        id: receipt.session_id,
                        final_path: receipt.final_path,
                        created_at,
                    });
                }
                Ok(None) => {}
                Err(error) => {
                    eprintln!("[whosaid] 忽略损坏的待确认录音 {session_id}：{error}");
                }
            }
        }
        previews.sort_by(|left, right| {
            right
                .created_at
                .total_cmp(&left.created_at)
                .then_with(|| right.id.cmp(&left.id))
        });
        Ok(previews)
    }

    pub fn acknowledge_preview(&self, id: &str) -> Result<(), RecordingError> {
        validate_session_id(id)?;
        let completed = self.secure_completed_root(false)?;
        let receipt_path = completed.join(format!("{id}.json"));
        // 先完整校验凭据及最终文件；验证失败绝不移除入口。
        self.read_receipt(id)?
            .ok_or_else(|| RecordingError::Io("待确认录音不存在".into()))?;
        fs::remove_file(receipt_path).map_err(io_error)
    }

    pub fn rename_pending_preview(
        &self,
        id: &str,
        requested_name: &str,
    ) -> Result<PendingRecordingPreview, RecordingError> {
        validate_session_id(id)?;
        let stem = validate_recording_name(requested_name)?;
        let (mut receipt, _) = self
            .read_receipt(id)?
            .ok_or_else(|| RecordingError::Io("待确认录音不存在".into()))?;
        let original = PathBuf::from(&receipt.final_path);
        let target = self.validate_final_path(
            &original
                .parent()
                .ok_or_else(|| RecordingError::Io("录音文件缺少父目录".into()))?
                .join(format!("{stem}.m4a")),
        )?;

        if target != original {
            fs::hard_link(&original, &target).map_err(|error| {
                if error.kind() == std::io::ErrorKind::AlreadyExists {
                    RecordingError::Io("该录音名称已存在，请换一个名称".into())
                } else {
                    io_error(error)
                }
            })?;
            receipt.final_path = path_string(&target);
            if let Err(error) = self.write_receipt(&receipt) {
                let _ = fs::remove_file(&target);
                return Err(error);
            }
            if let Err(error) = fs::remove_file(&original) {
                eprintln!(
                    "[whosaid] 录音改名后无法清理旧文件 {}：{error}",
                    original.display()
                );
            }
        }

        Ok(PendingRecordingPreview {
            id: receipt.session_id,
            final_path: receipt.final_path,
            created_at: receipt.created_at,
        })
    }

    pub(crate) fn reconcile_completed_session(
        &self,
        session_id: &str,
        final_path: &Path,
    ) -> Result<(), RecordingError> {
        validate_session_id(session_id)?;
        let receipt_path = self
            .load_receipt(session_id)?
            .ok_or_else(|| RecordingError::Io("录音完成凭据不存在".into()))?;
        if receipt_path != self.validate_final_path(final_path)? {
            return Err(RecordingError::Io("录音完成凭据路径不一致".into()));
        }

        let (_, incomplete_root) = match self.secure_roots(false) {
            Ok(roots) => roots,
            Err(RecordingError::Io(message)) if message.contains("不存在") => return Ok(()),
            Err(error) => return Err(error),
        };
        let session_dir = incomplete_root.join(session_id);
        match fs::symlink_metadata(&session_dir) {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
            Err(error) => return Err(io_error(error)),
            Ok(_) => validate_plain_directory(&session_dir, "待对账录音会话目录")?,
        }

        // 先把 receipt 中已校验的结果补回清单；只有这一步持久化成功，才允许删除
        // 原始音轨。清单写失败时调用方会保留恢复条目和全部素材。
        let recording = self
            .load_recording(session_id, true)?
            .ok_or_else(|| RecordingError::Io("待对账录音会话缺少清单".into()))?;
        self.complete_with_receipt(&recording, &receipt_path)?;
        self.remove_completed_session(&recording)
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
        let (_, incomplete_root) = self.secure_roots(false)?;
        let expected_dir = incomplete_root.join(session_id);
        if fs::canonicalize(session_dir).map_err(io_error)? != expected_dir {
            return Err(RecordingError::Io("原生会话目录不在受控录音目录内".into()));
        }

        let recording = self.recoverable_by_id(session_id)?;
        let native_system = fs::canonicalize(system_track).map_err(io_error)?;
        let expected_system = fs::canonicalize(&recording.system_track).map_err(io_error)?;
        let native_microphone = microphone_track
            .map(fs::canonicalize)
            .transpose()
            .map_err(io_error)?;
        let expected_microphone = recording
            .microphone_track
            .as_ref()
            .map(fs::canonicalize)
            .transpose()
            .map_err(io_error)?;
        if native_system != expected_system || native_microphone != expected_microphone {
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
        self.final_path_at(&stem, |path| fs::symlink_metadata(path).is_ok())
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
        self.secure_roots(true).map(|_| ())
    }

    pub(crate) fn planned_final_path(
        &self,
        recording: &RecoverableRecording,
    ) -> Result<PathBuf, RecordingError> {
        let (mut manifest, manifest_path) = self.read_manifest(&recording.session_id)?;
        if let Some(path) = &manifest.final_path {
            return self.validate_final_path(Path::new(path));
        }
        let path = self.validate_final_path(&self.final_path_for(recording))?;
        manifest.final_path = Some(path_string(&path));
        manifest.mix_stage = MixStage::Pending;
        manifest.final_size = None;
        manifest.final_sha256 = None;
        self.write_manifest(&manifest_path, &manifest)?;
        Ok(path)
    }

    pub(crate) fn move_to_next_final_path(
        &self,
        recording: &RecoverableRecording,
    ) -> Result<PathBuf, RecordingError> {
        let (mut manifest, manifest_path) = self.read_manifest(&recording.session_id)?;
        let path = self.validate_final_path(&self.final_path_for(recording))?;
        manifest.final_path = Some(path_string(&path));
        self.write_manifest(&manifest_path, &manifest)?;
        Ok(path)
    }

    pub(crate) fn record_ready_artifact(
        &self,
        recording: &RecoverableRecording,
        final_path: &Path,
        size: u64,
        sha256: &str,
    ) -> Result<(), RecordingError> {
        let (mut manifest, manifest_path) = self.read_manifest(&recording.session_id)?;
        let final_path = self.validate_final_path(final_path)?;
        manifest.final_path = Some(path_string(&final_path));
        manifest.final_size = Some(size);
        manifest.final_sha256 = Some(sha256.to_owned());
        manifest.mix_stage = MixStage::ReadyToInstall;
        self.write_manifest(&manifest_path, &manifest)
    }

    pub(crate) fn record_installed(
        &self,
        recording: &RecoverableRecording,
    ) -> Result<(), RecordingError> {
        let (mut manifest, manifest_path) = self.read_manifest(&recording.session_id)?;
        manifest.mix_stage = MixStage::Installed;
        self.write_manifest(&manifest_path, &manifest)
    }

    pub(crate) fn installed_result(
        &self,
        recording: &RecoverableRecording,
    ) -> Result<Option<PathBuf>, RecordingError> {
        let (manifest, _) = self.read_manifest(&recording.session_id)?;
        let (Some(path), Some(size), Some(expected_hash)) = (
            manifest.final_path,
            manifest.final_size,
            manifest.final_sha256,
        ) else {
            return Ok(None);
        };
        let path = self.validate_final_path(Path::new(&path))?;
        match artifact_matches(&path, size, &expected_hash) {
            Ok(true) => Ok(Some(path)),
            Ok(false) => Ok(None),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(error) => Err(io_error(error)),
        }
    }

    pub(crate) fn artifact_fingerprint(path: &Path) -> Result<(u64, String), RecordingError> {
        let mut file = fs::File::open(path).map_err(io_error)?;
        let size = file.metadata().map_err(io_error)?.len();
        let mut hasher = Sha256::new();
        let mut buffer = [0_u8; 64 * 1024];
        loop {
            let count = file.read(&mut buffer).map_err(io_error)?;
            if count == 0 {
                break;
            }
            hasher.update(&buffer[..count]);
        }
        Ok((size, format!("{:x}", hasher.finalize())))
    }

    pub(crate) fn complete_with_receipt(
        &self,
        recording: &RecoverableRecording,
        final_path: &Path,
    ) -> Result<(), RecordingError> {
        let final_path = self.validate_final_path(final_path)?;
        let (size, sha256) = Self::artifact_fingerprint(&final_path)?;
        let (mut manifest, manifest_path) = self.read_manifest(&recording.session_id)?;
        let receipt = CompletionReceipt {
            session_id: recording.session_id.clone(),
            final_path: path_string(&final_path),
            final_size: size,
            final_sha256: sha256.clone(),
            created_at: recording.started_at,
        };
        // 先落长期完成凭据，再标记/删除临时会话。任一点强杀后都至少保留一条
        // 可按 session id 找回的已校验 final path。
        self.write_receipt(&receipt)?;
        manifest.final_path = Some(path_string(&final_path));
        manifest.final_size = Some(size);
        manifest.final_sha256 = Some(sha256);
        manifest.mix_stage = MixStage::Complete;
        manifest.complete = true;
        self.write_manifest(&manifest_path, &manifest)
    }

    pub(crate) fn remove_completed_session(
        &self,
        recording: &RecoverableRecording,
    ) -> Result<(), RecordingError> {
        let session = self.paths_for_recording(recording)?;
        let (_, incomplete_root) = self.secure_roots(false)?;
        validate_plain_directory(&session.session_dir, "录音会话目录")?;
        let canonical = fs::canonicalize(&session.session_dir).map_err(io_error)?;
        if canonical.parent() != Some(incomplete_root.as_path()) {
            return Err(RecordingError::Io("拒绝删除受控录音目录外的路径".into()));
        }
        fs::remove_dir_all(session.session_dir).map_err(io_error)
    }

    pub(crate) fn discard_empty_session(&self, session_id: &str) -> Result<(), RecordingError> {
        validate_session_id(session_id)?;
        let (_, incomplete_root) = self.secure_roots(false)?;
        let session_dir = incomplete_root.join(session_id);
        validate_plain_directory(&session_dir, "待清理空会话目录")?;
        let canonical = fs::canonicalize(&session_dir).map_err(io_error)?;
        if canonical.parent() != Some(incomplete_root.as_path()) {
            return Err(RecordingError::Io("拒绝清理受控录音目录外的路径".into()));
        }
        if fs::read_dir(&canonical).map_err(io_error)?.next().is_some() {
            return Err(RecordingError::Io(
                "会话目录已有录音数据，拒绝按空会话清理".into(),
            ));
        }
        fs::remove_dir(canonical).map_err(io_error)
    }

    fn read_manifest(
        &self,
        session_id: &str,
    ) -> Result<(SessionManifest, PathBuf), RecordingError> {
        validate_session_id(session_id)?;
        let (_, incomplete_root) = self.secure_roots(false)?;
        let session_dir = incomplete_root.join(session_id);
        validate_plain_directory(&session_dir, "恢复会话目录")?;
        let canonical = fs::canonicalize(&session_dir).map_err(io_error)?;
        if canonical.parent() != Some(incomplete_root.as_path()) {
            return Err(RecordingError::Io("会话清单目录已离开受控目录".into()));
        }
        let path = canonical.join(MANIFEST_NAME);
        validate_regular_file(&path, "会话清单")?;
        let bytes = fs::read(&path).map_err(io_error)?;
        let manifest = serde_json::from_slice(&bytes)
            .map_err(|error| RecordingError::Io(format!("无法读取会话清单：{error}")))?;
        Ok((manifest, path))
    }

    fn write_manifest(
        &self,
        manifest_path: &Path,
        manifest: &SessionManifest,
    ) -> Result<(), RecordingError> {
        let encoded = serde_json::to_vec(manifest)
            .map_err(|error| RecordingError::Io(format!("无法更新会话清单：{error}")))?;
        atomic_write(manifest_path, &encoded)
    }

    fn load_recording(
        &self,
        session_id: &str,
        allow_complete: bool,
    ) -> Result<Option<RecoverableRecording>, RecordingError> {
        validate_session_id(session_id)?;
        let (_, incomplete_root) = self.secure_roots(false)?;
        let session_dir = incomplete_root.join(session_id);
        validate_plain_directory(&session_dir, "恢复会话目录")?;
        let canonical_session = fs::canonicalize(&session_dir).map_err(io_error)?;
        if canonical_session.parent() != Some(incomplete_root.as_path()) {
            return Err(RecordingError::Io("恢复会话目录已离开受控目录".into()));
        }
        let manifest_path = session_dir.join(MANIFEST_NAME);
        let manifest_bytes = fs::read(&manifest_path).map_err(io_error)?;
        let manifest: SessionManifest = serde_json::from_slice(&manifest_bytes)
            .map_err(|error| RecordingError::Io(format!("无法读取会话清单：{error}")))?;
        if manifest.complete && !allow_complete {
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
        let (_, incomplete_root) = self.secure_roots(false)?;
        let expected_dir = incomplete_root.join(&recording.session_id);
        if Path::new(&recording.session_dir) != expected_dir {
            return Err(RecordingError::Io("恢复会话路径已离开受控目录".into()));
        }
        Ok(SessionPaths {
            session_id: recording.session_id.clone(),
            session_dir: expected_dir,
        })
    }

    fn validate_final_path(&self, path: &Path) -> Result<PathBuf, RecordingError> {
        let (root, _) = self.secure_roots(true)?;
        let parent = path
            .parent()
            .ok_or_else(|| RecordingError::Io("最终录音路径缺少父目录".into()))?;
        let canonical_parent = fs::canonicalize(parent).map_err(io_error)?;
        let filename = path
            .file_name()
            .ok_or_else(|| RecordingError::Io("最终录音路径缺少安全文件名".into()))?;
        if canonical_parent != root
            || path.extension().and_then(|value| value.to_str()) != Some("m4a")
            || Path::new(filename).components().count() != 1
        {
            return Err(RecordingError::Io("最终录音路径已离开录音根目录".into()));
        }
        Ok(root.join(filename))
    }

    fn secure_completed_root(&self, create: bool) -> Result<PathBuf, RecordingError> {
        ensure_plain_directory(&self.root, "录音根目录", create)?;
        let root = fs::canonicalize(&self.root).map_err(io_error)?;
        let completed = self.root.join(COMPLETED_DIRECTORY);
        ensure_plain_directory(&completed, "录音完成凭据目录", create)?;
        let completed = fs::canonicalize(completed).map_err(io_error)?;
        if completed.parent() != Some(root.as_path()) {
            return Err(RecordingError::Io(
                "录音完成凭据目录已离开录音根目录".into(),
            ));
        }
        Ok(completed)
    }

    fn write_receipt(&self, receipt: &CompletionReceipt) -> Result<(), RecordingError> {
        validate_session_id(&receipt.session_id)?;
        let completed = self.secure_completed_root(true)?;
        let path = completed.join(format!("{}.json", receipt.session_id));
        let encoded = serde_json::to_vec(receipt)
            .map_err(|error| RecordingError::Io(format!("无法写入录音完成凭据：{error}")))?;
        atomic_write(&path, &encoded)
    }

    fn load_receipt(&self, session_id: &str) -> Result<Option<PathBuf>, RecordingError> {
        Ok(self
            .read_receipt(session_id)?
            .map(|(receipt, _)| PathBuf::from(receipt.final_path)))
    }

    fn read_receipt(
        &self,
        session_id: &str,
    ) -> Result<Option<(CompletionReceipt, PathBuf)>, RecordingError> {
        validate_session_id(session_id)?;
        let completed = match self.secure_completed_root(false) {
            Ok(path) => path,
            Err(RecordingError::Io(message)) if message.contains("不存在") => return Ok(None),
            Err(error) => return Err(error),
        };
        let receipt_path = completed.join(format!("{session_id}.json"));
        match fs::symlink_metadata(&receipt_path) {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(io_error(error)),
            Ok(metadata) if !metadata.is_file() || metadata.file_type().is_symlink() => {
                return Err(RecordingError::Io("录音完成凭据不是普通文件".into()))
            }
            Ok(_) => {}
        }
        let mut receipt: CompletionReceipt =
            serde_json::from_slice(&fs::read(&receipt_path).map_err(io_error)?)
                .map_err(|error| RecordingError::Io(format!("无法读取录音完成凭据：{error}")))?;
        if receipt.session_id != session_id {
            return Err(RecordingError::Io("录音完成凭据会话 ID 不一致".into()));
        }
        let final_path = self.validate_final_path(Path::new(&receipt.final_path))?;
        if !artifact_matches(&final_path, receipt.final_size, &receipt.final_sha256)
            .map_err(io_error)?
        {
            return Err(RecordingError::Io("录音完成凭据对应文件已变化".into()));
        }
        // 规范化后再回填，调用者永远拿不到凭据中未经校验的原始路径。
        receipt.final_path = path_string(&final_path);
        Ok(Some((receipt, receipt_path)))
    }

    fn secure_roots(&self, create: bool) -> Result<(PathBuf, PathBuf), RecordingError> {
        ensure_plain_directory(&self.root, "录音根目录", create)?;
        let canonical_root = fs::canonicalize(&self.root).map_err(io_error)?;
        let incomplete = self.root.join(INCOMPLETE_DIRECTORY);
        ensure_plain_directory(&incomplete, "未完成录音目录", create)?;
        let canonical_incomplete = fs::canonicalize(&incomplete).map_err(io_error)?;
        if canonical_incomplete.parent() != Some(canonical_root.as_path()) {
            return Err(RecordingError::Io("未完成录音目录已离开录音根目录".into()));
        }
        Ok((canonical_root, canonical_incomplete))
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

fn validate_recording_name(requested_name: &str) -> Result<String, RecordingError> {
    let trimmed = requested_name.trim();
    let stem = trimmed
        .strip_suffix(".m4a")
        .or_else(|| trimmed.strip_suffix(".M4A"))
        .unwrap_or(trimmed)
        .trim();
    if stem.is_empty()
        || stem == "."
        || stem == ".."
        || stem.chars().count() > 80
        || stem
            .chars()
            .any(|character| character.is_control() || matches!(character, '/' | '\\' | ':'))
    {
        return Err(RecordingError::Io(
            "录音名称不能为空、不能包含路径字符，且最多 80 个字".into(),
        ));
    }
    Ok(stem.to_owned())
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

fn ensure_plain_directory(path: &Path, label: &str, create: bool) -> Result<(), RecordingError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_dir() => {
            Err(RecordingError::Io(format!("{label}不是普通目录")))
        }
        Ok(_) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound && create => {
            match fs::create_dir(path) {
                Ok(()) => validate_plain_directory(path, label),
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                    validate_plain_directory(path, label)
                }
                Err(error) => Err(io_error(error)),
            }
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            Err(RecordingError::Io(format!("{label}不存在")))
        }
        Err(error) => Err(io_error(error)),
    }
}

fn validate_plain_directory(path: &Path, label: &str) -> Result<(), RecordingError> {
    let metadata = fs::symlink_metadata(path).map_err(io_error)?;
    if !metadata.is_dir() || metadata.file_type().is_symlink() {
        return Err(RecordingError::Io(format!("{label}不是普通目录")));
    }
    Ok(())
}

fn atomic_write(path: &Path, content: &[u8]) -> Result<(), RecordingError> {
    let parent = path
        .parent()
        .ok_or_else(|| RecordingError::Io("原子写入路径缺少父目录".into()))?;
    validate_plain_directory(parent, "原子写入目录")?;
    let temporary = parent.join(format!(".{}.tmp", Uuid::new_v4()));
    let result = (|| {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)
            .map_err(io_error)?;
        file.write_all(content).map_err(io_error)?;
        file.sync_all().map_err(io_error)?;
        fs::rename(&temporary, path).map_err(io_error)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

fn artifact_matches(path: &Path, size: u64, sha256: &str) -> std::io::Result<bool> {
    let metadata = fs::symlink_metadata(path)?;
    if !metadata.is_file() || metadata.file_type().is_symlink() || metadata.len() != size {
        return Ok(false);
    }
    RecordingStore::artifact_fingerprint(path)
        .map(|(_, actual)| actual == sha256)
        .map_err(|error| std::io::Error::other(error.to_string()))
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

    #[cfg(unix)]
    use std::os::unix::fs::symlink;

    #[test]
    fn begin_session_stays_under_uuid_incomplete_directory() {
        let root = tempdir().unwrap();
        let store = RecordingStore::new(root.path().to_path_buf());

        let session = store.begin_session(Local::now()).unwrap();

        assert_eq!(
            session.session_dir.parent(),
            Some(
                fs::canonicalize(root.path().join(".incomplete"))
                    .unwrap()
                    .as_path()
            )
        );
        assert!(uuid::Uuid::parse_str(&session.session_id).is_ok());
        assert!(session.session_dir.is_dir());
    }

    #[cfg(unix)]
    #[test]
    fn symlink_recordings_root_is_rejected() {
        let holder = tempdir().unwrap();
        let outside = tempdir().unwrap();
        let root = holder.path().join("recordings");
        symlink(outside.path(), &root).unwrap();

        let store = RecordingStore::new(root);
        assert!(store.begin_session(Local::now()).is_err());
        assert!(fs::read_dir(outside.path()).unwrap().next().is_none());
    }

    #[cfg(unix)]
    #[test]
    fn symlink_incomplete_directory_is_rejected() {
        let root = tempdir().unwrap();
        let outside = tempdir().unwrap();
        symlink(outside.path(), root.path().join(".incomplete")).unwrap();

        let store = RecordingStore::new(root.path().to_path_buf());
        assert!(store.begin_session(Local::now()).is_err());
        assert!(fs::read_dir(outside.path()).unwrap().next().is_none());
    }

    #[cfg(unix)]
    #[test]
    fn symlink_session_directory_is_rejected_without_deleting_target() {
        let root = tempdir().unwrap();
        let outside = tempdir().unwrap();
        let store = RecordingStore::new(root.path().to_path_buf());
        store.begin_session(Local::now()).unwrap();
        let session_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
        fs::write(outside.path().join("keep.txt"), b"keep").unwrap();
        symlink(
            outside.path(),
            fs::canonicalize(root.path().join(".incomplete"))
                .unwrap()
                .join(session_id),
        )
        .unwrap();

        assert!(store.recoverable_by_id(session_id).is_err());
        assert_eq!(fs::read(outside.path().join("keep.txt")).unwrap(), b"keep");
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

    #[test]
    fn completed_receipt_is_authoritative_pending_preview_across_restart() {
        let root = tempdir().unwrap();
        let store = RecordingStore::new(root.path().to_path_buf());
        let session_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
        let incomplete = root.path().join(".incomplete");
        fs::create_dir_all(&incomplete).unwrap();
        write_session(&incomplete, session_id, false);
        let recording = store.recoverable_by_id(session_id).unwrap();
        let final_path = root.path().join("meeting.m4a");
        fs::write(&final_path, b"final audio").unwrap();

        store
            .complete_with_receipt(&recording, &final_path)
            .unwrap();
        store.remove_completed_session(&recording).unwrap();

        let restarted = RecordingStore::new(root.path().to_path_buf());
        assert_eq!(
            restarted.pending_previews().unwrap(),
            vec![PendingRecordingPreview {
                id: session_id.into(),
                final_path: path_string(&fs::canonicalize(&final_path).unwrap()),
                created_at: 1786492215.0,
            },]
        );
    }

    #[test]
    fn legacy_receipt_without_created_at_uses_receipt_mtime() {
        let root = tempdir().unwrap();
        let store = RecordingStore::new(root.path().to_path_buf());
        let session_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
        let final_path = root.path().join("legacy.m4a");
        fs::write(&final_path, b"legacy audio").unwrap();
        let (final_size, final_sha256) = RecordingStore::artifact_fingerprint(&final_path).unwrap();
        let completed = root.path().join(COMPLETED_DIRECTORY);
        fs::create_dir(&completed).unwrap();
        let receipt_path = completed.join(format!("{session_id}.json"));
        fs::write(
            &receipt_path,
            serde_json::json!({
                "sessionId": session_id,
                "finalPath": path_string(&fs::canonicalize(&final_path).unwrap()),
                "finalSize": final_size,
                "finalSha256": final_sha256
            })
            .to_string(),
        )
        .unwrap();
        let receipt_mtime = receipt_path
            .metadata()
            .unwrap()
            .modified()
            .unwrap()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs_f64();

        let previews = store.pending_previews().unwrap();
        assert_eq!(previews.len(), 1);
        assert_eq!(previews[0].id, session_id);
        assert!((previews[0].created_at - receipt_mtime).abs() < 0.001);
    }

    #[test]
    fn multiple_pending_previews_acknowledge_only_selected_receipt() {
        let root = tempdir().unwrap();
        let store = RecordingStore::new(root.path().to_path_buf());
        let incomplete = root.path().join(".incomplete");
        fs::create_dir_all(&incomplete).unwrap();
        for (session_id, filename) in [
            ("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "one.m4a"),
            ("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "two.m4a"),
        ] {
            write_session(&incomplete, session_id, false);
            let recording = store.recoverable_by_id(session_id).unwrap();
            let final_path = root.path().join(filename);
            fs::write(&final_path, filename.as_bytes()).unwrap();
            store
                .complete_with_receipt(&recording, &final_path)
                .unwrap();
            store.remove_completed_session(&recording).unwrap();
        }

        assert_eq!(store.pending_previews().unwrap().len(), 2);
        store
            .acknowledge_preview("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
            .unwrap();
        let remaining = store.pending_previews().unwrap();
        assert_eq!(remaining.len(), 1);
        assert_eq!(remaining[0].id, "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb");
    }

    #[test]
    fn pending_previews_are_newest_first() {
        let root = tempdir().unwrap();
        let store = RecordingStore::new(root.path().to_path_buf());
        let incomplete = root.path().join(".incomplete");
        fs::create_dir_all(&incomplete).unwrap();
        for (session_id, filename, started_at) in [
            (
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "older.m4a",
                1_786_492_000.0,
            ),
            (
                "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                "newer.m4a",
                1_786_495_600.0,
            ),
        ] {
            write_session_at(&incomplete, session_id, false, started_at);
            let recording = store.recoverable_by_id(session_id).unwrap();
            let final_path = root.path().join(filename);
            fs::write(&final_path, filename.as_bytes()).unwrap();
            store
                .complete_with_receipt(&recording, &final_path)
                .unwrap();
            store.remove_completed_session(&recording).unwrap();
        }

        let previews = store.pending_previews().unwrap();
        assert_eq!(previews.len(), 2);
        assert_eq!(previews[0].id, "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb");
        assert_eq!(previews[1].id, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
    }

    #[test]
    fn renaming_pending_preview_updates_receipt_without_overwriting() {
        let root = tempdir().unwrap();
        let store = RecordingStore::new(root.path().to_path_buf());
        let incomplete = root.path().join(".incomplete");
        let session_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
        fs::create_dir_all(&incomplete).unwrap();
        write_session(&incomplete, session_id, false);
        let recording = store.recoverable_by_id(session_id).unwrap();
        let original = root.path().join("2026-08-14_10-00-00.m4a");
        fs::write(&original, b"meeting audio").unwrap();
        store.complete_with_receipt(&recording, &original).unwrap();

        let renamed = store
            .rename_pending_preview(session_id, "腾讯会议产品复盘")
            .unwrap();

        let renamed_path = fs::canonicalize(root.path())
            .unwrap()
            .join("腾讯会议产品复盘.m4a");
        assert_eq!(renamed.final_path, path_string(&renamed_path));
        assert!(!original.exists());
        assert_eq!(fs::read(&renamed.final_path).unwrap(), b"meeting audio");
        assert_eq!(store.pending_previews().unwrap(), vec![renamed]);

        fs::write(root.path().join("已存在.m4a"), b"keep me").unwrap();
        assert!(store.rename_pending_preview(session_id, "已存在").is_err());
        assert_eq!(
            fs::read(root.path().join("已存在.m4a")).unwrap(),
            b"keep me"
        );
    }

    #[test]
    fn renaming_pending_preview_rejects_unsafe_or_blank_names() {
        let root = tempdir().unwrap();
        let store = RecordingStore::new(root.path().to_path_buf());
        for name in ["", "   ", "../escape", "folder/name", "a\\b", ".", ".."] {
            assert!(store
                .rename_pending_preview("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", name)
                .is_err());
        }
    }

    #[test]
    fn receipt_persistence_failure_keeps_session_materials() {
        let root = tempdir().unwrap();
        let store = RecordingStore::new(root.path().to_path_buf());
        let session_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
        let incomplete = root.path().join(".incomplete");
        fs::create_dir_all(&incomplete).unwrap();
        write_session(&incomplete, session_id, false);
        let recording = store.recoverable_by_id(session_id).unwrap();
        let final_path = root.path().join("meeting.m4a");
        fs::write(&final_path, b"final audio").unwrap();
        fs::write(root.path().join(COMPLETED_DIRECTORY), b"blocked").unwrap();

        assert!(store
            .complete_with_receipt(&recording, &final_path)
            .is_err());
        assert!(Path::new(&recording.session_dir).is_dir());
        assert!(Path::new(&recording.system_track).is_file());
        assert!(store.recoverable_by_id(session_id).is_ok());
    }

    #[test]
    fn failed_acknowledgement_keeps_pending_preview() {
        let root = tempdir().unwrap();
        let store = RecordingStore::new(root.path().to_path_buf());
        let incomplete = root.path().join(".incomplete");
        let session_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
        fs::create_dir_all(&incomplete).unwrap();
        write_session(&incomplete, session_id, false);
        let recording = store.recoverable_by_id(session_id).unwrap();
        let final_path = root.path().join("meeting.m4a");
        fs::write(&final_path, b"final audio").unwrap();
        store
            .complete_with_receipt(&recording, &final_path)
            .unwrap();

        assert!(store
            .acknowledge_preview("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
            .is_err());
        assert_eq!(store.pending_previews().unwrap().len(), 1);
    }

    fn write_session(root: &std::path::Path, session_id: &str, complete: bool) {
        write_session_at(root, session_id, complete, 1786492215.0);
    }

    fn write_session_at(root: &std::path::Path, session_id: &str, complete: bool, started_at: f64) {
        let session = root.join(session_id);
        fs::create_dir_all(&session).unwrap();
        fs::write(session.join("system.caf"), b"audio").unwrap();
        fs::write(
            session.join("session.json"),
            serde_json::json!({
                "schemaVersion": 1,
                "sessionId": session_id,
                "startedAt": started_at,
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
