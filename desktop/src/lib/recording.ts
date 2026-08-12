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

export interface RecordingController {
  start: typeof startRecording;
  stop: typeof stopRecording;
  watch: typeof watchRecording;
}

export const startRecording = () =>
  invoke<RecordingSnapshot>("start_recording");

export const stopRecording = () =>
  invoke<RecordingStopResult>("stop_recording");

// Tauri 2 的 listen 异步返回取消监听函数；调用方必须等待此 Promise 后再保存/调用 unlisten。
export const watchRecording = (
  fn: (snapshot: RecordingSnapshot) => void,
): Promise<UnlistenFn> =>
  listen<RecordingSnapshot>("recording://state", (event) => fn(event.payload));

export const recordingController: RecordingController = {
  start: startRecording,
  stop: stopRecording,
  watch: watchRecording,
};
