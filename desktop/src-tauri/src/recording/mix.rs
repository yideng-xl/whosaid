use std::fmt;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use super::storage::{RecordingStore, RecoverableRecording};

const DUAL_TRACK_FILTER: &str = "[0:a]aresample=48000:async=1:first_pts=0[sys];[1:a]aresample=48000:async=1:first_pts=0[mic];[sys][mic]amix=inputs=2:duration=first:dropout_transition=0,alimiter=limit=0.95[out]";

#[derive(Clone, Debug)]
pub struct FfmpegTools {
    pub ffmpeg: PathBuf,
    pub ffprobe: PathBuf,
}

impl FfmpegTools {
    pub fn new(ffmpeg: PathBuf, ffprobe: PathBuf) -> Self {
        Self { ffmpeg, ffprobe }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct MixError {
    message: String,
}

impl MixError {
    fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

impl fmt::Display for MixError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl std::error::Error for MixError {}

pub fn build_mix_args(
    system_track: &Path,
    microphone_track: Option<&Path>,
    output: &Path,
) -> Vec<String> {
    let mut args = vec![
        "-y".into(),
        "-i".into(),
        system_track.to_string_lossy().into_owned(),
    ];
    if let Some(microphone_track) = microphone_track {
        args.extend([
            "-i".into(),
            microphone_track.to_string_lossy().into_owned(),
            "-filter_complex".into(),
            DUAL_TRACK_FILTER.into(),
            "-map".into(),
            "[out]".into(),
        ]);
    }
    args.extend([
        "-ar".into(),
        "48000".into(),
        "-ac".into(),
        "1".into(),
        "-c:a".into(),
        "aac".into(),
        "-b:a".into(),
        "128k".into(),
        output.to_string_lossy().into_owned(),
    ]);
    args
}

pub fn build_probe_args(output: &Path) -> Vec<String> {
    vec![
        "-v".into(),
        "error".into(),
        "-show_entries".into(),
        "format=duration".into(),
        "-of".into(),
        "default=noprint_wrappers=1:nokey=1".into(),
        output.to_string_lossy().into_owned(),
    ]
}

pub fn mix_recording(
    tools: &FfmpegTools,
    store: &RecordingStore,
    recording: &RecoverableRecording,
) -> Result<PathBuf, MixError> {
    let current = store
        .recoverable_by_id(&recording.session_id)
        .map_err(|error| MixError::new(error.to_string()))?;
    if current != *recording {
        return Err(MixError::new("录音恢复信息已变化，请重新扫描后再试"));
    }
    store
        .ensure_root()
        .map_err(|error| MixError::new(error.to_string()))?;

    let final_path = store.final_path_for(&current);
    let temporary_path = store.temporary_path_for(&current, &final_path);
    let _ = fs::remove_file(&temporary_path);
    let mix_args = build_mix_args(
        Path::new(&current.system_track),
        current.microphone_track.as_deref().map(Path::new),
        &temporary_path,
    );
    let mix_output = Command::new(&tools.ffmpeg)
        .args(&mix_args)
        .output()
        .map_err(|error| {
            MixError::new(format!(
                "无法启动 FFmpeg（{}）：{error}",
                tools.ffmpeg.display()
            ))
        })?;
    if !mix_output.status.success() {
        let _ = fs::remove_file(&temporary_path);
        return Err(MixError::new(format!(
            "FFmpeg 混音失败：{}",
            stderr_summary(&mix_output.stderr)
        )));
    }

    let probe_output = Command::new(&tools.ffprobe)
        .args(build_probe_args(&temporary_path))
        .output()
        .map_err(|error| {
            let _ = fs::remove_file(&temporary_path);
            MixError::new(format!(
                "无法启动 ffprobe（{}）：{error}",
                tools.ffprobe.display()
            ))
        })?;
    if !probe_output.status.success() {
        let _ = fs::remove_file(&temporary_path);
        return Err(MixError::new(format!(
            "ffprobe 校验失败：{}",
            stderr_summary(&probe_output.stderr)
        )));
    }
    let duration = String::from_utf8_lossy(&probe_output.stdout)
        .trim()
        .parse::<f64>()
        .map_err(|error| {
            let _ = fs::remove_file(&temporary_path);
            MixError::new(format!("ffprobe 返回了无效时长：{error}"))
        })?;
    if !duration.is_finite() || duration <= 0.0 {
        let _ = fs::remove_file(&temporary_path);
        return Err(MixError::new("ffprobe 检测到录音时长为零"));
    }

    fs::rename(&temporary_path, &final_path).map_err(|error| {
        let _ = fs::remove_file(&temporary_path);
        MixError::new(format!("无法保存最终录音：{error}"))
    })?;
    if let Err(error) = store.mark_recording_complete(&current) {
        // 清单没能完成落盘时，撤回最终文件，避免调用方收到失败后重试出重复录音。
        let _ = fs::remove_file(&final_path);
        return Err(MixError::new(error.to_string()));
    }
    if let Err(error) = store.remove_completed_session(&current) {
        // 最终文件和 complete=true 清单均已安全落盘。残留目录不会再次出现在恢复列表，
        // 清理失败不应把一个有效的录音结果降级成可重试失败。
        eprintln!("[whosaid] 无法清理已完成录音目录：{error}");
    }
    Ok(final_path)
}

fn stderr_summary(stderr: &[u8]) -> String {
    let text = String::from_utf8_lossy(stderr);
    let compact = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if compact.is_empty() {
        return "未提供错误详情".into();
    }
    let chars: Vec<char> = compact.chars().collect();
    let start = chars.len().saturating_sub(2_000);
    chars[start..].iter().collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;
    use std::process::Command;
    use tempfile::tempdir;

    #[test]
    fn mic_missing_builds_system_only_command() {
        let args = build_mix_args(Path::new("system.caf"), None, Path::new("out.tmp.m4a"));
        assert!(!args.iter().any(|arg| arg.contains("amix=inputs=2")));
        assert!(args.iter().any(|arg| arg == "-c:a"));
        assert_eq!(args.last().unwrap(), "out.tmp.m4a");
    }

    #[test]
    fn two_tracks_build_amix_command() {
        let args = build_mix_args(
            Path::new("system.caf"),
            Some(Path::new("microphone.caf")),
            Path::new("out.tmp.m4a"),
        );
        assert!(args.iter().any(|arg| arg.contains("amix=inputs=2")));
        assert!(args.iter().any(|arg| arg == "-filter_complex"));
        assert_eq!(args.last().unwrap(), "out.tmp.m4a");
    }

    #[cfg(unix)]
    #[test]
    fn failed_mix_keeps_tracks_and_manifest() {
        let root = tempdir().unwrap();
        let store = RecordingStore::new(root.path().to_path_buf());
        let session_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
        let session_dir = write_manifest(root.path(), session_id, false, false);
        let recording = store.recoverable_by_id(session_id).unwrap();
        let tools = FfmpegTools::new("/usr/bin/false".into(), "/usr/bin/false".into());

        assert!(mix_recording(&tools, &store, &recording).is_err());
        assert!(session_dir.join("system.caf").is_file());
        assert!(session_dir.join("session.json").is_file());
    }

    #[test]
    #[ignore = "需要系统或包内 FFmpeg/ffprobe"]
    fn mixes_generated_short_tracks() {
        let root = tempdir().unwrap();
        let session_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
        let session_dir = write_manifest(root.path(), session_id, false, true);
        generate_track("ffmpeg", &session_dir.join("system.caf"), 440);
        generate_track("ffmpeg", &session_dir.join("microphone.caf"), 660);

        let store = RecordingStore::new(root.path().to_path_buf());
        let recording = store.recoverable_by_id(session_id).unwrap();
        let tools = FfmpegTools::new("ffmpeg".into(), "ffprobe".into());
        let final_path = mix_recording(&tools, &store, &recording).unwrap();

        assert!(final_path.is_file());
        assert!(!session_dir.exists());
        let probe = Command::new("ffprobe")
            .args(build_probe_args(&final_path))
            .output()
            .unwrap();
        assert!(probe.status.success());
        assert!(
            String::from_utf8_lossy(&probe.stdout)
                .trim()
                .parse::<f64>()
                .unwrap()
                > 0.0
        );
    }

    #[test]
    #[ignore = "需要系统或包内 FFmpeg/ffprobe"]
    fn mixes_generated_short_tracks_without_microphone() {
        let root = tempdir().unwrap();
        let session_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
        let session_dir = write_manifest(root.path(), session_id, false, false);
        generate_track("ffmpeg", &session_dir.join("system.caf"), 440);

        let store = RecordingStore::new(root.path().to_path_buf());
        let recording = store.recoverable_by_id(session_id).unwrap();
        let tools = FfmpegTools::new("ffmpeg".into(), "ffprobe".into());
        let final_path = mix_recording(&tools, &store, &recording).unwrap();

        assert!(final_path.is_file());
        assert!(!session_dir.exists());
    }

    fn write_manifest(root: &Path, session_id: &str, complete: bool, microphone: bool) -> PathBuf {
        let session_dir = root.join(".incomplete").join(session_id);
        fs::create_dir_all(&session_dir).unwrap();
        fs::write(session_dir.join("system.caf"), b"not empty").unwrap();
        if microphone {
            fs::write(session_dir.join("microphone.caf"), b"not empty").unwrap();
        }
        fs::write(
            session_dir.join("session.json"),
            serde_json::json!({
                "schemaVersion": 1,
                // 原生桥自行生成清单 ID，和 Rust 受控目录 ID 不要求相同。
                "sessionId": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                "startedAt": 1786492215.0,
                "systemTrack": "system.caf",
                "microphoneTrack": microphone.then_some("microphone.caf"),
                "systemStatus": "stopped",
                "microphoneStatus": if microphone { "stopped" } else { "unavailable" },
                "complete": complete
            })
            .to_string(),
        )
        .unwrap();
        session_dir
    }

    fn generate_track(ffmpeg: &str, path: &Path, frequency: u16) {
        let output = Command::new(ffmpeg)
            .args([
                "-y",
                "-f",
                "lavfi",
                "-i",
                &format!("sine=frequency={frequency}:duration=0.25"),
                "-ar",
                "48000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                &path.to_string_lossy(),
            ])
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "生成测试音轨失败：{}",
            String::from_utf8_lossy(&output.stderr)
        );
    }
}
