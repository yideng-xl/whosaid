import type { JobSummary } from "./api";
import type {
  RecoverableRecording,
  RecordingSnapshot,
} from "./recording";

export interface RecordingSubmissionApi {
  submitJob(
    audioPath: string,
    numSpeakers?: number,
    idempotencyKey?: string,
  ): Promise<string>;
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
  idempotencyKey?: string,
): Promise<RecordingSubmissionResult> {
  const validatedPath = validateFinalPath(finalPath);
  try {
    const rawJobId = idempotencyKey
      ? await api.submitJob(validatedPath, undefined, idempotencyKey)
      : await api.submitJob(validatedPath);
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
  context: boolean | {
    submissionFailureTerminal?: boolean;
    submissionFailureFinalPath?: string | null;
  } = false,
): RecordingSnapshot {
  if (context === true) return current;
  if (typeof context === "object" && context.submissionFailureTerminal) {
    const protectedFinalPath =
      context.submissionFailureFinalPath?.trim() ||
      current.final_path?.trim() ||
      null;
    if (protectedFinalPath) {
      if (["ready", "mixing", "stopping", "recording"].includes(incoming.phase)) {
        return current;
      }
      if (incoming.phase === "failed") {
        return {
          ...incoming,
          final_path: protectedFinalPath,
          recoverable_paths: [
            protectedFinalPath,
            ...incoming.recoverable_paths.filter(
              (path) => path.trim() && path.trim() !== protectedFinalPath,
            ),
          ],
          error: incoming.error ?? current.error,
        };
      }
    }
  }
  if (
    current.phase === "submitting" &&
    incoming.phase === "ready" &&
    current.final_path === incoming.final_path
  ) {
    return current;
  }
  return incoming;
}

export interface RecordingEventGateState {
  ignoreRecordingEvents: boolean;
  submissionFailureFinalPath: string | null;
}

export function transitionRecordingSubmissionFailure(
  snapshot: RecordingSnapshot,
  error: unknown,
  eventGate: RecordingEventGateState,
): {
  snapshot: RecordingSnapshot;
  eventGate: RecordingEventGateState;
} {
  const errorFinalPath =
    error instanceof RecordingSubmissionError
      ? error.finalPath.trim() || null
      : null;
  const protectedFinalPath =
    errorFinalPath ??
    eventGate.submissionFailureFinalPath?.trim() ??
    snapshot.final_path?.trim() ??
    null;
  const errorMessage = error instanceof Error ? error.message : String(error);

  return {
    snapshot: {
      ...snapshot,
      phase: "failed",
      final_path: protectedFinalPath,
      recoverable_paths: protectedFinalPath
        ? [protectedFinalPath]
        : snapshot.recoverable_paths.filter((path) => path.trim()),
      error: errorMessage,
    },
    eventGate: {
      ignoreRecordingEvents: false,
      submissionFailureFinalPath: protectedFinalPath,
    },
  };
}

interface SnapshotWaiter {
  afterRevision: number;
  resolve: (snapshot: RecordingSnapshot) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

export class RecordingSnapshotCoordinator {
  private currentSnapshot: RecordingSnapshot;
  private currentRevision = 0;
  private terminalError: Error | null = null;
  private waiters = new Set<SnapshotWaiter>();

  constructor(initialSnapshot: RecordingSnapshot) {
    this.currentSnapshot = initialSnapshot;
  }

  get snapshot(): RecordingSnapshot {
    return this.currentSnapshot;
  }

  get revision(): number {
    return this.currentRevision;
  }

  publishLocal(snapshot: RecordingSnapshot): boolean {
    return this.advance(snapshot);
  }

  publishBackend(
    snapshot: RecordingSnapshot,
    context: boolean | {
      submissionFailureTerminal?: boolean;
      submissionFailureFinalPath?: string | null;
    } = false,
  ): boolean {
    const merged = mergeBackendRecordingSnapshot(
      this.currentSnapshot,
      snapshot,
      context,
    );
    if (merged === this.currentSnapshot) return false;
    return this.advance(merged);
  }

  waitForAdvance(
    afterRevision: number,
    timeoutMs = 30_000,
  ): Promise<RecordingSnapshot> {
    if (this.terminalError) return Promise.reject(this.terminalError);
    if (this.currentRevision > afterRevision) {
      return Promise.resolve(this.currentSnapshot);
    }
    return new Promise((resolve, reject) => {
      const waiter: SnapshotWaiter = {
        afterRevision,
        resolve,
        reject,
        timer: setTimeout(() => {
          this.waiters.delete(waiter);
          reject(new RecordingFlowError("等待录音状态更新超时"));
        }, timeoutMs),
      };
      this.waiters.add(waiter);
    });
  }

  fail(error: unknown): void {
    this.rejectAll(
      error instanceof Error ? error : new RecordingFlowError(String(error)),
    );
  }

  cancel(message = "录音状态等待已取消"): void {
    this.rejectAll(new RecordingFlowError(message));
  }

  private advance(snapshot: RecordingSnapshot): boolean {
    if (snapshot === this.currentSnapshot) return false;
    this.currentSnapshot = snapshot;
    this.currentRevision += 1;
    for (const waiter of [...this.waiters]) {
      if (this.currentRevision <= waiter.afterRevision) continue;
      this.waiters.delete(waiter);
      clearTimeout(waiter.timer);
      waiter.resolve(snapshot);
    }
    return true;
  }

  private rejectAll(error: Error): void {
    this.terminalError = error;
    for (const waiter of this.waiters) {
      clearTimeout(waiter.timer);
      waiter.reject(error);
    }
    this.waiters.clear();
  }
}

export class RecordingSubmissionRegistry {
  private readonly inFlight = new Map<
    string,
    Promise<RecordingSubmissionResult>
  >();
  private readonly acceptanceInFlight = new Map<
    string,
    Promise<RecordingSubmissionResult>
  >();

  submit(
    finalPath: string,
    api: RecordingSubmissionApi,
    idempotencyKey?: string,
  ): Promise<RecordingSubmissionResult> {
    let validatedPath: string;
    try {
      validatedPath = validateFinalPath(finalPath);
    } catch (error) {
      return Promise.reject(error);
    }
    // 无键调用代表一次明确的手工提交，不按路径单飞；用户可以主动对同一文件再转一次。
    if (!idempotencyKey) return submitFinalizedRecording(validatedPath, api);
    const existing = this.inFlight.get(validatedPath);
    if (existing) return existing;

    const task = submitFinalizedRecording(validatedPath, api, idempotencyKey);
    this.inFlight.set(validatedPath, task);
    void task.then(
      () => this.clear(validatedPath, task),
      () => this.clear(validatedPath, task),
    );
    return task;
  }

  get(finalPath: string): Promise<RecordingSubmissionResult> | undefined {
    return this.inFlight.get(validateFinalPath(finalPath));
  }

  getAccepted(finalPath: string): Promise<RecordingSubmissionResult> | undefined {
    const validatedPath = validateFinalPath(finalPath);
    return (
      this.acceptanceInFlight.get(validatedPath) ??
      this.inFlight.get(validatedPath)
    );
  }

  submitAndAccept(
    finalPath: string,
    api: RecordingSubmissionApi,
    accept: (result: RecordingSubmissionResult) => void | Promise<void>,
    idempotencyKey?: string,
  ): Promise<RecordingSubmissionResult> {
    let validatedPath: string;
    try {
      validatedPath = validateFinalPath(finalPath);
    } catch (error) {
      return Promise.reject(error);
    }
    if (!idempotencyKey) {
      return submitFinalizedRecording(validatedPath, api).then(async (result) => {
        await accept(result);
        return result;
      });
    }
    const existing = this.acceptanceInFlight.get(validatedPath);
    if (existing) return existing;

    const task = this.submit(validatedPath, api, idempotencyKey).then(async (result) => {
      await accept(result);
      return result;
    });
    this.acceptanceInFlight.set(validatedPath, task);
    void task.then(
      () => this.clearAcceptance(validatedPath, task),
      () => this.clearAcceptance(validatedPath, task),
    );
    return task;
  }

  private clear(
    finalPath: string,
    task: Promise<RecordingSubmissionResult>,
  ): void {
    if (this.inFlight.get(finalPath) === task) this.inFlight.delete(finalPath);
  }

  private clearAcceptance(
    finalPath: string,
    task: Promise<RecordingSubmissionResult>,
  ): void {
    if (this.acceptanceInFlight.get(finalPath) === task) {
      this.acceptanceInFlight.delete(finalPath);
    }
  }
}

export class RecordingCloseGuard {
  private inFlight: Promise<void> | null = null;

  run(action: () => Promise<void>): Promise<void> {
    if (this.inFlight) return this.inFlight;
    let task: Promise<void>;
    try {
      task = Promise.resolve(action());
    } catch (error) {
      task = Promise.reject(error);
    }
    this.inFlight = task;
    void task.then(
      () => this.clear(task),
      () => this.clear(task),
    );
    return task;
  }

  private clear(task: Promise<void>): void {
    if (this.inFlight === task) this.inFlight = null;
  }
}

export interface RecoverableRecordingItem extends RecoverableRecording {
  busy: boolean;
  error: string | null;
}

export interface PendingRecordingSubmission {
  key: string;
  label: string;
  finalPath: string;
  idempotencyKey?: string;
  busy: boolean;
  error: string | null;
}

interface RecordingSubmissionStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export interface PersistedRecordingSubmission {
  label: string;
  finalPath: string;
  idempotencyKey: string;
}

const RECORDING_SUBMISSIONS_STORAGE_KEY =
  "whosaid.recording-submissions.v1";

/**
 * 录音最终文件的提交意图。先持久化再发 POST，响应丢失或应用重启后仍复用同一键。
 * 这里不接管普通拖入文件，避免把“同一路径再次手工转写”误判成重复请求。
 */
export class RecordingSubmissionKeyStore {
  private memory: PersistedRecordingSubmission[] | null = null;
  private storageReadable = true;

  constructor(
    private readonly storage: RecordingSubmissionStorage,
    private readonly makeKey: () => string = () =>
      `recording:${crypto.randomUUID()}`,
  ) {}

  list(): PersistedRecordingSubmission[] {
    if (this.memory !== null) return [...this.memory];
    let encoded: string | null;
    try {
      encoded = this.storage.getItem(RECORDING_SUBMISSIONS_STORAGE_KEY);
    } catch {
      this.storageReadable = false;
      this.memory = [];
      return [];
    }
    if (!encoded) {
      this.memory = [];
      return [];
    }
    try {
      const decoded: unknown = JSON.parse(encoded);
      if (!Array.isArray(decoded)) {
        this.memory = [];
        return [];
      }
      const byPath = new Map<string, PersistedRecordingSubmission>();
      for (const candidate of decoded) {
        if (!candidate || typeof candidate !== "object") continue;
        const value = candidate as Record<string, unknown>;
        const finalPath =
          typeof value.finalPath === "string" ? value.finalPath.trim() : "";
        const idempotencyKey =
          typeof value.idempotencyKey === "string"
            ? value.idempotencyKey.trim()
            : "";
        const label = typeof value.label === "string" && value.label.trim()
          ? value.label.trim()
          : "录音结果";
        if (!finalPath || !idempotencyKey) continue;
        byPath.set(finalPath, { finalPath, idempotencyKey, label });
      }
      this.memory = [...byPath.values()];
      return [...this.memory];
    } catch {
      this.memory = [];
      return [];
    }
  }

  prepare(finalPath: string, label: string): PersistedRecordingSubmission {
    const normalizedPath = validateFinalPath(finalPath);
    const existing = this.list().find(
      (submission) => submission.finalPath === normalizedPath,
    );
    if (existing) return existing;
    const idempotencyKey = this.makeKey().trim();
    if (!idempotencyKey) {
      throw new RecordingFlowError("无法生成录音转写提交标识");
    }
    const submission = {
      finalPath: normalizedPath,
      label: label.trim() || "录音结果",
      idempotencyKey,
    };
    this.persist([...this.list(), submission]);
    return submission;
  }

  complete(finalPath: string): void {
    const normalizedPath = validateFinalPath(finalPath);
    const remaining = this.list().filter(
      (submission) => submission.finalPath !== normalizedPath,
    );
    // HTTP 已被服务端接纳且页面副作用已完成：持久清理只能 best-effort，不能反向
    // 把成功改判为失败。当前会话立即以内存清单为准，避免再次展示/提交。
    this.memory = remaining;
    if (!this.storageReadable) return;
    try {
      if (remaining.length === 0) {
        this.storage.removeItem(RECORDING_SUBMISSIONS_STORAGE_KEY);
      } else {
        this.storage.setItem(
          RECORDING_SUBMISSIONS_STORAGE_KEY,
          JSON.stringify(remaining),
        );
      }
    } catch {
      this.storageReadable = false;
    }
  }

  private persist(submissions: PersistedRecordingSubmission[]): void {
    const previous = this.memory === null ? this.list() : [...this.memory];
    this.memory = [...submissions];
    if (!this.storageReadable) return;
    if (submissions.length === 0) {
      this.storage.removeItem(RECORDING_SUBMISSIONS_STORAGE_KEY);
      return;
    }
    try {
      this.storage.setItem(
        RECORDING_SUBMISSIONS_STORAGE_KEY,
        JSON.stringify(submissions),
      );
    } catch (error) {
      // 首次提交键若未持久化，不能继续 POST；恢复旧内存态供当前页面保留重提入口。
      this.memory = previous;
      throw error;
    }
  }
}

/** 只有页面的全部接纳副作用成功后，才清理本次录音的持久化提交键。 */
export async function completeRecordingAcceptance(
  result: RecordingSubmissionResult,
  keyStore: RecordingSubmissionKeyStore,
  accept: (result: RecordingSubmissionResult) => void | Promise<void>,
): Promise<void> {
  await accept(result);
  keyStore.complete(result.audioPath);
}

export function completeAcceptedRecordingSubmission(
  snapshot: RecordingSnapshot,
  submissions: PendingRecordingSubmission[],
  result: RecordingSubmissionResult,
  submissionFailureFinalPath: string | null,
): {
  snapshot: RecordingSnapshot;
  pending: PendingRecordingSubmission[];
  submissionFailureFinalPath: string | null;
} {
  const acceptedPath = validateFinalPath(result.audioPath);
  const protectedFinalPath = submissionFailureFinalPath?.trim() || null;
  const currentFinalPath = snapshot.final_path?.trim() || null;
  const settlesCurrentRecording = protectedFinalPath
    ? protectedFinalPath === acceptedPath
    : currentFinalPath === acceptedPath;

  return {
    snapshot: settlesCurrentRecording
      ? {
          ...snapshot,
          phase: "idle",
          elapsed_seconds: 0,
          final_path: null,
          recoverable_paths: [],
          error: null,
        }
      : snapshot,
    pending: submissions.filter(
      (pending) => pending.finalPath.trim() !== acceptedPath,
    ),
    submissionFailureFinalPath:
      protectedFinalPath === acceptedPath ? null : protectedFinalPath,
  };
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
  const normalizedPath = validateFinalPath(next.finalPath);
  const normalizedNext = {
    ...next,
    key: `path:${normalizedPath}`,
    finalPath: normalizedPath,
  };
  const index = submissions.findIndex(
    (submission) => submission.finalPath === normalizedPath,
  );
  if (index < 0) return [...submissions, normalizedNext];
  return submissions.map((submission, currentIndex) =>
    currentIndex === index ? normalizedNext : submission,
  );
}

export function removePendingRecordingSubmission(
  submissions: PendingRecordingSubmission[],
  key: string,
): PendingRecordingSubmission[] {
  return submissions.filter((submission) => submission.key !== key);
}

export function retainFailedRecordingSubmission(
  snapshot: RecordingSnapshot,
  submissions: PendingRecordingSubmission[],
  failure: {
    key: string;
    label: string;
    finalPath: string;
    idempotencyKey?: string;
    error: unknown;
  },
): {
  snapshot: RecordingSnapshot;
  pending: PendingRecordingSubmission[];
} {
  const finalPath = validateFinalPath(failure.finalPath);
  const error =
    failure.error instanceof Error
      ? failure.error.message
      : String(failure.error);
  return {
    snapshot: {
      ...snapshot,
      phase: "failed",
      final_path: finalPath,
      recoverable_paths: [finalPath],
      error,
    },
    pending: upsertPendingRecordingSubmission(submissions, {
      key: failure.key,
      label: failure.label,
      finalPath,
      idempotencyKey: failure.idempotencyKey,
      busy: false,
      error,
    }),
  };
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
