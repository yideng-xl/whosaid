import type {
  RecordingPhase,
  RecordingSnapshot,
  SourceStatus,
} from "./recording";

export interface RecordingUiState extends RecordingSnapshot {
  warning: string | null;
}

const MICROPHONE_WARNING =
  "没有录到你的麦克风声音。你可以继续会议，系统声音不会中断。";
const MICROPHONE_RECONNECTING_WARNING =
  "麦克风设备切换中，正在重新连接；系统声音不会中断。";

const PHASE_LABELS: Record<RecordingPhase, string> = {
  idle: "准备录音",
  requesting_permissions: "正在请求录音权限",
  starting: "正在开始录音",
  recording: "正在录音",
  stopping: "正在保存录音",
  mixing: "正在合成音轨",
  ready: "录音已保存",
  submitting: "已开始转写",
  failed: "录音失败",
};

export function recordingState(): RecordingUiState {
  return {
    phase: "idle",
    elapsed_seconds: 0,
    system_audio: "pending",
    microphone: "pending",
    final_path: null,
    recoverable_paths: [],
    error: null,
    warning: null,
  };
}

export function labelForPhase(phase: RecordingPhase): string {
  return PHASE_LABELS[phase];
}

export function sourceLabel(status: SourceStatus): string {
  const labels: Record<SourceStatus, string> = {
    pending: "等待中",
    active: "录制中",
    unavailable: "不可用",
    denied: "未授权",
    interrupted: "重新连接中",
  };
  return labels[status];
}

export function formatElapsed(totalSeconds: number): string {
  const safeSeconds = Math.max(0, Math.floor(totalSeconds));
  const hours = Math.floor(safeSeconds / 3600);
  const minutes = Math.floor((safeSeconds % 3600) / 60);
  const seconds = safeSeconds % 60;
  const base = `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  return hours > 0 ? `${String(hours).padStart(2, "0")}:${base}` : base;
}

export function reduceRecordingState(
  current: RecordingUiState,
  update: Partial<RecordingSnapshot>,
): RecordingUiState {
  const next = { ...current, ...update };
  const microphoneDegraded = ["unavailable", "denied", "interrupted"].includes(
    next.microphone,
  );
  const showMicrophoneWarning =
    next.system_audio === "active" &&
    microphoneDegraded &&
    ["recording", "stopping"].includes(next.phase);
  const warning = showMicrophoneWarning
    ? next.microphone === "interrupted"
      ? MICROPHONE_RECONNECTING_WARNING
      : MICROPHONE_WARNING
    : null;

  return { ...next, warning };
}
