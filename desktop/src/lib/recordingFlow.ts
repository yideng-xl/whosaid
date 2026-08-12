import type { JobSummary } from "./api";
import type {
  RecoverableRecording,
  RecordingSnapshot,
} from "./recording";

export interface RecordingSubmissionApi {
  submitJob(audioPath: string): Promise<string>;
}

export interface RecordingSubmissionResult {
  jobId: string;
  audioPath: string;
}

export class RecordingSubmissionError extends Error {
  readonly finalPath: string;
  override readonly cause: unknown;

  constructor(finalPath: string, cause: unknown) {
    super(`录音已保存，但提交转写失败：${String(cause)}`);
    this.name = "RecordingSubmissionError";
    this.finalPath = finalPath;
    this.cause = cause;
  }
}

export class RecordingFlowError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RecordingFlowError";
  }
}

export function validateFinalPath(finalPath: string): string {
  const validatedPath = finalPath.trim();
  if (!validatedPath) {
    throw new RecordingFlowError("最终录音路径为空，无法提交转写");
  }
  return validatedPath;
}

export async function submitFinalizedRecording(
  finalPath: string,
  api: RecordingSubmissionApi,
): Promise<RecordingSubmissionResult> {
  const validatedPath = validateFinalPath(finalPath);
  try {
    const rawJobId = await api.submitJob(validatedPath);
    const jobId = rawJobId.trim();
    if (!jobId) throw new Error("转写服务返回的任务 ID 为空");
    return { jobId, audioPath: validatedPath };
  } catch (error) {
    throw new RecordingSubmissionError(validatedPath, error);
  }
}

export async function finalizeAndSubmit(
  stop: () => Promise<{ final_path: string }>,
  api: RecordingSubmissionApi,
): Promise<RecordingSubmissionResult> {
  const { final_path: finalPath } = await stop();
  return submitFinalizedRecording(finalPath, api);
}

export function createRecordingJob(
  result: RecordingSubmissionResult,
  createdAt = Date.now() / 1000,
): JobSummary {
  return {
    id: result.jobId,
    status: "queued",
    progress: 0,
    error: null,
    audio_path: result.audioPath,
    created_at: createdAt,
  };
}

export function prependRecordingJob(
  jobs: JobSummary[],
  job: JobSummary,
): JobSummary[] {
  return [job, ...jobs.filter((existing) => existing.id !== job.id)];
}

export function mergeBackendRecordingSnapshot(
  current: RecordingSnapshot,
  incoming: RecordingSnapshot,
  finalizationAccepted = false,
): RecordingSnapshot {
  if (finalizationAccepted) return current;
  if (
    current.phase === "submitting" &&
    incoming.phase === "ready" &&
    current.final_path === incoming.final_path
  ) {
    return current;
  }
  return incoming;
}

export interface RecoverableRecordingItem extends RecoverableRecording {
  busy: boolean;
  error: string | null;
}

export interface PendingRecordingSubmission {
  key: string;
  label: string;
  finalPath: string;
  busy: boolean;
  error: string | null;
}

export function makeRecoverableRecordingItems(
  recordings: RecoverableRecording[],
): RecoverableRecordingItem[] {
  return recordings.map((recording) => ({
    ...recording,
    busy: false,
    error: null,
  }));
}

export function beginRecoverableRecording(
  recordings: RecoverableRecordingItem[],
  sessionId: string,
): RecoverableRecordingItem[] {
  return recordings.map((recording) =>
    recording.sessionId === sessionId
      ? { ...recording, busy: true, error: null }
      : recording,
  );
}

export function failRecoverableRecording(
  recordings: RecoverableRecordingItem[],
  sessionId: string,
  error: string,
): RecoverableRecordingItem[] {
  return recordings.map((recording) =>
    recording.sessionId === sessionId
      ? { ...recording, busy: false, error }
      : recording,
  );
}

export function completeRecoverableRecording(
  recordings: RecoverableRecordingItem[],
  sessionId: string,
): RecoverableRecordingItem[] {
  return recordings.filter((recording) => recording.sessionId !== sessionId);
}

export function upsertPendingRecordingSubmission(
  submissions: PendingRecordingSubmission[],
  next: PendingRecordingSubmission,
): PendingRecordingSubmission[] {
  const index = submissions.findIndex((submission) => submission.key === next.key);
  if (index < 0) return [...submissions, next];
  return submissions.map((submission, currentIndex) =>
    currentIndex === index ? next : submission,
  );
}

export function removePendingRecordingSubmission(
  submissions: PendingRecordingSubmission[],
  key: string,
): PendingRecordingSubmission[] {
  return submissions.filter((submission) => submission.key !== key);
}

export interface RecordingCloseDependencies {
  stopAndSubmit(): Promise<void>;
  submitFinalPath(path: string): Promise<void>;
  waitForSnapshot(): Promise<RecordingSnapshot>;
  close(): Promise<void>;
}

export async function runRecordingCloseFlow(
  initialSnapshot: RecordingSnapshot,
  dependencies: RecordingCloseDependencies,
): Promise<void> {
  let snapshot = initialSnapshot;
  const activeCloseFlow = [
    "requesting_permissions",
    "starting",
    "recording",
    "stopping",
    "mixing",
    "submitting",
  ].includes(snapshot.phase);

  for (;;) {
    switch (snapshot.phase) {
      case "starting":
      case "recording":
        await dependencies.stopAndSubmit();
        await dependencies.close();
        return;
      case "requesting_permissions":
      case "stopping":
      case "mixing":
      case "submitting":
        snapshot = await dependencies.waitForSnapshot();
        break;
      case "ready":
        if (activeCloseFlow) {
          const finalPath = snapshot.final_path?.trim();
          if (!finalPath) {
            throw new RecordingFlowError("录音已保存，但最终录音路径为空");
          }
          await dependencies.submitFinalPath(finalPath);
        }
        await dependencies.close();
        return;
      case "failed":
        if (activeCloseFlow) {
          throw new RecordingFlowError(snapshot.error ?? "录音保存失败");
        }
        await dependencies.close();
        return;
      case "idle":
        await dependencies.close();
        return;
    }
  }
}
