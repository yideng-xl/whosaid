import type { JobSummary } from "./api";
import type { RecordingSnapshot } from "./recording";

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

export async function submitFinalizedRecording(
  finalPath: string,
  api: RecordingSubmissionApi,
): Promise<RecordingSubmissionResult> {
  try {
    const jobId = await api.submitJob(finalPath);
    return { jobId, audioPath: finalPath };
  } catch (error) {
    throw new RecordingSubmissionError(finalPath, error);
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
