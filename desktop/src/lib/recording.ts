import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

export type RecordingPhase =
  | "idle"
  | "requesting_permissions"
  | "starting"
  | "recording"
  | "stopping"
  | "mixing"
  | "ready"
  | "submitting"
  | "failed";

export type SourceStatus =
  | "pending"
  | "active"
  | "unavailable"
  | "denied"
  | "interrupted";

export interface RecordingSnapshot {
  phase: RecordingPhase;
  elapsed_seconds: number;
  system_audio: SourceStatus;
  microphone: SourceStatus;
  final_path: string | null;
  recoverable_paths: string[];
  error: string | null;
}

export interface RecordingStopResult {
  final_path: string;
}

export type PermissionStatus = "granted" | "notDetermined" | "denied";

export interface RecordingPermissions {
  systemAudio: PermissionStatus;
  microphone: PermissionStatus;
}

export type RecordingSettingsPane = "systemAudio" | "microphone";

export interface RecoverableRecording {
  sessionId: string;
  startedAt: number;
  sessionDir: string;
  systemTrack: string;
  microphoneTrack: string | null;
}

export interface RecordingController {
  start: typeof startRecording;
  stop: typeof stopRecording;
  watch: typeof watchRecording;
}

export interface AppCapabilities {
  directRecording: boolean;
}

export const getAppCapabilities = () =>
  invoke<AppCapabilities>("get_app_capabilities");

/**
 * 录音是平台专属能力：只有 Rust 明确确认支持后才允许页面注册监听或读取录音状态。
 * 能力读取失败时按不支持处理，避免其他平台短暂显示入口或误触权限请求。
 */
export async function initializeDirectRecording(
  initialize: () => void,
  getCapabilities: () => Promise<AppCapabilities> = getAppCapabilities,
): Promise<boolean> {
  let capabilities: AppCapabilities;
  try {
    capabilities = await getCapabilities();
  } catch {
    return false;
  }
  if (!capabilities.directRecording) return false;
  initialize();
  return true;
}

export const startRecording = () =>
  invoke<RecordingSnapshot>("start_recording");

export const stopRecording = () =>
  invoke<RecordingStopResult>("stop_recording");

export const getRecordingState = () =>
  invoke<RecordingSnapshot>("get_recording_state");

export const getRecordingPermissions = () =>
  invoke<RecordingPermissions>("get_recording_permissions");

export const openRecordingSettings = (pane: RecordingSettingsPane) =>
  invoke<void>("open_recording_settings", { pane });

export const listRecoverableRecordings = () =>
  invoke<RecoverableRecording[]>("list_recoverable_recordings");

export const retryRecordingMix = (sessionId: string) =>
  invoke<RecordingStopResult>("retry_recording_mix", { sessionId });

export const closeAfterRecording = () =>
  invoke<void>("close_after_recording");

// Tauri 2 的 listen 异步返回取消监听函数；调用方必须等待此 Promise 后再保存/调用 unlisten。
export const watchRecording = (
  fn: (snapshot: RecordingSnapshot) => void,
): Promise<UnlistenFn> =>
  listen<RecordingSnapshot>("recording://state", (event) => fn(event.payload));

export const watchRecordingCloseRequested = (
  fn: () => void,
): Promise<UnlistenFn> =>
  listen("recording://close-requested", () => fn());

/**
 * 把 Tauri 2 异步注册监听的 Promise 变成可同步调用的清理函数。
 * 页面先销毁时，晚返回的 unlisten 会立即执行；注册拒绝也始终被消费。
 */
export function manageAsyncListener(
  registration: Promise<UnlistenFn>,
  onError: (error: unknown) => void,
): () => void {
  let disposed = false;
  let unlisten: UnlistenFn | null = null;

  void registration
    .then((registeredUnlisten) => {
      if (disposed) {
        registeredUnlisten();
      } else {
        unlisten = registeredUnlisten;
      }
    })
    .catch((error) => {
      if (!disposed) onError(error);
    });

  return () => {
    if (disposed) return;
    disposed = true;
    unlisten?.();
    unlisten = null;
  };
}

export const recordingController: RecordingController = {
  start: startRecording,
  stop: stopRecording,
  watch: watchRecording,
};
