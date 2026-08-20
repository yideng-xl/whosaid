import { describe, expect, it, vi } from "vitest";
import {
  acceptRecordingJobById,
  beginRecoverableRecording,
  completeAcceptedRecordingSubmission,
  completeRecordingPreviewAcceptance,
  completeRecordingAcceptance,
  completeRecoverableRecording,
  createRecordingJob,
  failRecoverableRecording,
  finalizeAndSubmit,
  makeRecoverableRecordingItems,
  pendingSubmissionsFromPreviews,
  mergeBackendRecordingSnapshot,
  prependRecordingJob,
  prepareRecordingPreview,
  removePendingRecordingSubmission,
  RecordingCloseGuard,
  RecordingSnapshotCoordinator,
  RecordingSubmissionError,
  RecordingSubmissionKeyStore,
  RecordingSubmissionRegistry,
  retainFailedRecordingSubmission,
  runRecoverableRecordingAction,
  runRecordingCloseFlow,
  saveRecordingForPreview,
  shouldSubscribeRecordingJob,
  submitFinalizedRecording,
  transitionRecordingSubmissionFailure,
  upsertPendingRecordingSubmission,
  type PendingRecordingSubmission,
} from "./recordingFlow";
import type { RecordingSnapshot } from "./recording";
import { recordingState } from "./recordingState";

describe("录音结束后的试听确认", () => {
  class MemoryStorage {
    private values = new Map<string, string>();
    getItem(key: string) { return this.values.get(key) ?? null; }
    setItem(key: string, value: string) { this.values.set(key, value); }
    removeItem(key: string) { this.values.delete(key); }
  }

  it("停止后只持久化待确认录音，不调用转写接口", async () => {
    const storage = new MemoryStorage();
    const keyStore = new RecordingSubmissionKeyStore(
      storage,
      () => "recording:preview",
    );
    const api = { submitJob: vi.fn() };

    const pending = await saveRecordingForPreview(
      async () => ({ final_path: " /recordings/preview.m4a " }),
      keyStore,
      "录音结果",
    );

    expect(api.submitJob).not.toHaveBeenCalled();
    expect(pending).toMatchObject({
      finalPath: "/recordings/preview.m4a",
      idempotencyKey: "recording:preview",
      busy: false,
      error: null,
    });
    expect(new RecordingSubmissionKeyStore(storage).list()).toEqual([
      expect.objectContaining({
        finalPath: "/recordings/preview.m4a",
        idempotencyKey: "recording:preview",
      }),
    ]);
  });

  it("首次开始转写时冻结并持久化词库选择，后续重试不改写", () => {
    const storage = new MemoryStorage();
    const keys = new RecordingSubmissionKeyStore(storage, () => "recording:vocab");
    keys.prepare("/recordings/vocab.m4a", "会议录音");

    const frozen = keys.prepare(
      "/recordings/vocab.m4a", "会议录音", ["names", "jiguan", "names"],
    );
    const retry = keys.prepare(
      "/recordings/vocab.m4a", "会议录音", ["names", "wangguan"],
    );

    expect(frozen.vocabularyLibraryIds).toEqual(["names", "jiguan"]);
    expect(retry.vocabularyLibraryIds).toEqual(["names", "jiguan"]);
    expect(new RecordingSubmissionKeyStore(storage).list()[0].vocabularyLibraryIds)
      .toEqual(["names", "jiguan"]);
  });

  it("恢复混音成功也只生成待确认录音，不调用转写接口", () => {
    const storage = new MemoryStorage();
    const keyStore = new RecordingSubmissionKeyStore(
      storage,
      () => "recording:recovered",
    );
    const api = { submitJob: vi.fn() };
    expect(
      prepareRecordingPreview(
        "/recordings/recovered.m4a",
        keyStore,
        "10:30 的录音",
      ),
    ).toMatchObject({
      finalPath: "/recordings/recovered.m4a",
      idempotencyKey: "recording:recovered",
    });
    expect(api.submitJob).not.toHaveBeenCalled();
  });

  it("重启只以后端preview为权威，忽略localStorage孤儿并恢复多条", () => {
    const storage = new MemoryStorage();
    const keys = new RecordingSubmissionKeyStore(storage, () => "recording:ghost");
    keys.prepare("/recordings/ghost.m4a", "旧孤儿");
    keys.prepare("/recordings/one.m4a", "one.m4a");
    const restored = pendingSubmissionsFromPreviews(
      [
        { id: "one", finalPath: "/recordings/one.m4a", createdAt: 100 },
        { id: "two", finalPath: "/recordings/two.m4a", createdAt: 200 },
      ],
      keys,
    );
    expect(restored.map((item) => item.finalPath)).toEqual([
      "/recordings/one.m4a",
      "/recordings/two.m4a",
    ]);
    expect(restored.map((item) => item.previewId)).toEqual(["one", "two"]);
    expect(restored[0].label).toMatch(/^录音时间 /);
  });

  it("localStorage写失败仍恢复后端preview，但不生成可提交幂等键", () => {
    const storage = new MemoryStorage();
    storage.setItem = () => { throw new Error("quota exceeded"); };
    const keys = new RecordingSubmissionKeyStore(storage, () => "recording:key");
    const restored = pendingSubmissionsFromPreviews(
      [{ id: "one", finalPath: "/recordings/one.m4a", createdAt: 100 }],
      keys,
    );
    expect(restored).toEqual([
      expect.objectContaining({
        previewId: "one",
        finalPath: "/recordings/one.m4a",
        idempotencyKey: undefined,
        error: expect.stringContaining("quota exceeded"),
      }),
    ]);
  });

  it("提交最终文件并返回新任务", async () => {
    const api = { submitJob: vi.fn().mockResolvedValue("job-recorded") };
    const result = await finalizeAndSubmit(
      async () => ({ final_path: "/recordings/2026-08-12_10-30-15.m4a" }),
      api,
    );
    expect(api.submitJob).toHaveBeenCalledWith(
      "/recordings/2026-08-12_10-30-15.m4a",
    );
    expect(result).toEqual({
      jobId: "job-recorded",
      audioPath: "/recordings/2026-08-12_10-30-15.m4a",
    });
  });

  it("提交失败时保留已经落盘的最终路径", async () => {
    const api = { submitJob: vi.fn().mockRejectedValue(new Error("offline")) };
    await expect(
      finalizeAndSubmit(
        async () => ({ final_path: "/recordings/meeting.m4a" }),
        api,
      ),
    ).rejects.toMatchObject({ finalPath: "/recordings/meeting.m4a" });
  });

  it("拒绝空的最终路径且不调用提交接口", async () => {
    const api = { submitJob: vi.fn() };
    await expect(
      finalizeAndSubmit(async () => ({ final_path: "   " }), api),
    ).rejects.toThrow("最终录音路径为空");
    expect(api.submitJob).not.toHaveBeenCalled();
  });

  it("空任务ID视为提交失败并保留已验证路径", async () => {
    const api = { submitJob: vi.fn().mockResolvedValue("  ") };
    await expect(
      submitFinalizedRecording("  /recordings/meeting.m4a  ", api),
    ).rejects.toMatchObject({
      finalPath: "/recordings/meeting.m4a",
      message: expect.stringContaining("任务 ID 为空"),
    });
    expect(api.submitJob).toHaveBeenCalledWith("/recordings/meeting.m4a");
  });

  it("重新提交已有最终文件时不再停止或混音", async () => {
    const api = { submitJob: vi.fn().mockResolvedValue("job-retry") };
    await expect(
      submitFinalizedRecording("/recordings/meeting.m4a", api),
    ).resolves.toEqual({
      jobId: "job-retry",
      audioPath: "/recordings/meeting.m4a",
    });
    expect(api.submitJob).toHaveBeenCalledOnce();
  });

  it("录音提交失败及应用重启后复用同一持久化幂等键", async () => {
    const storage = new MemoryStorage();
    const firstStore = new RecordingSubmissionKeyStore(
      storage,
      () => "recording:stable-key",
    );
    const pending = firstStore.prepare("/recordings/restart.m4a", "录音结果");
    const failingApi = { submitJob: vi.fn().mockRejectedValue(new Error("response lost")) };

    await expect(
      submitFinalizedRecording(pending.finalPath, failingApi, pending.idempotencyKey),
    ).rejects.toBeInstanceOf(RecordingSubmissionError);
    expect(failingApi.submitJob).toHaveBeenCalledWith(
      "/recordings/restart.m4a",
      undefined,
      "recording:stable-key",
    );

    // 新实例代表应用重启；恢复条目和重试都必须沿用已落盘的键。
    const restartedStore = new RecordingSubmissionKeyStore(storage, () => "unexpected");
    expect(restartedStore.list()).toEqual([pending]);
    const retryApi = { submitJob: vi.fn().mockResolvedValue("job-existing") };
    await submitFinalizedRecording(
      pending.finalPath,
      retryApi,
      restartedStore.prepare(pending.finalPath, "录音结果").idempotencyKey,
    );
    expect(retryApi.submitJob).toHaveBeenCalledWith(
      "/recordings/restart.m4a",
      undefined,
      "recording:stable-key",
    );
  });

  it("录音接纳后清掉持久化条目，普通拖入不自动携带幂等键", async () => {
    const storage = new MemoryStorage();
    const keyStore = new RecordingSubmissionKeyStore(storage, () => "recording:key");
    keyStore.prepare("/recordings/a.m4a", "录音结果");
    keyStore.complete("/recordings/a.m4a");
    expect(new RecordingSubmissionKeyStore(storage, () => "other").list()).toEqual([]);

    const api = { submitJob: vi.fn().mockResolvedValue("job-manual") };
    await submitFinalizedRecording("/imports/a.m4a", api);
    expect(api.submitJob).toHaveBeenCalledWith("/imports/a.m4a");
  });

  it("页面接纳失败时不ack且保留幂等键", async () => {
    const storage = new MemoryStorage();
    const keyStore = new RecordingSubmissionKeyStore(storage, () => "recording:key");
    const pending = keyStore.prepare("/recordings/a.m4a", "a.m4a");
    const result = { jobId: "job-a", audioPath: pending.finalPath };
    const acknowledge = vi.fn();

    await expect(
      completeRecordingPreviewAcceptance(
        result,
        "preview-a",
        keyStore,
        acknowledge,
        vi.fn().mockRejectedValue(new Error("subscribe failed")),
      ),
    ).rejects.toThrow("subscribe failed");
    expect(acknowledge).not.toHaveBeenCalled();
    expect(keyStore.list()).toEqual([pending]);
  });

  it("ack失败时任务已接纳，但receipt与幂等键仍保留", async () => {
    const storage = new MemoryStorage();
    const keyStore = new RecordingSubmissionKeyStore(storage, () => "recording:key");
    const pending = keyStore.prepare("/recordings/a.m4a", "a.m4a");
    const result = { jobId: "job-a", audioPath: pending.finalPath };
    const accept = vi.fn();

    const order: string[] = [];
    await expect(completeRecordingPreviewAcceptance(
      result, "preview-a", keyStore,
      async () => { order.push("ack"); throw new Error("ack failed"); },
      async () => { order.push("accept"); accept(); },
    )).rejects.toThrow("ack failed");
    expect(order).toEqual(["accept", "ack"]);
    expect(accept).toHaveBeenCalledOnce();
    expect(keyStore.list()).toEqual([pending]);
  });

  it("ack失败后用同一key重试只保留一个任务和订阅，ack成功才清key", async () => {
    const storage = new MemoryStorage();
    const keyStore = new RecordingSubmissionKeyStore(storage, () => "recording:stable");
    const pending = keyStore.prepare("/recordings/a.m4a", "a.m4a");
    const api = { submitJob: vi.fn().mockResolvedValue("job-existing") };
    const registry = new RecordingSubmissionRegistry();
    const jobs: ReturnType<typeof createRecordingJob>[] = [];
    const watching = new Set<string>();
    const subscribe = vi.fn((jobId: string) => watching.add(jobId));
    const acceptIdempotently = (result: { jobId: string; audioPath: string }) => {
      const job = createRecordingJob(result, 100);
      jobs.splice(0, jobs.length, ...prependRecordingJob(jobs, job));
      if (!watching.has(job.id)) subscribe(job.id);
    };
    const acknowledge = vi.fn()
      .mockRejectedValueOnce(new Error("ack failed"))
      .mockResolvedValueOnce(undefined);

    const run = () => registry.submitAndAccept(
      pending.finalPath,
      api,
      (result) => completeRecordingPreviewAcceptance(
        result, "preview-a", keyStore, acknowledge, acceptIdempotently,
      ),
      pending.idempotencyKey,
    );
    await expect(run()).rejects.toThrow("ack failed");
    await expect(run()).resolves.toMatchObject({ jobId: "job-existing" });

    expect(api.submitJob).toHaveBeenCalledTimes(2);
    expect(api.submitJob).toHaveBeenNthCalledWith(
      2, pending.finalPath, undefined, pending.idempotencyKey,
    );
    expect(jobs).toHaveLength(1);
    expect(subscribe).toHaveBeenCalledOnce();
    expect(acknowledge).toHaveBeenCalledTimes(2);
    expect(keyStore.list()).toEqual([]);
  });

  it("ack成功后本地key清理失败不把已接纳任务改判为失败", async () => {
    const storage = new MemoryStorage();
    const keyStore = new RecordingSubmissionKeyStore(storage, () => "recording:key");
    keyStore.prepare("/recordings/a.m4a", "a.m4a");
    storage.removeItem = () => { throw new Error("remove denied"); };
    const accept = vi.fn();
    const acknowledge = vi.fn().mockResolvedValue(undefined);

    await expect(completeRecordingPreviewAcceptance(
      { jobId: "job-a", audioPath: "/recordings/a.m4a" },
      "preview-a",
      keyStore,
      acknowledge,
      accept,
    )).resolves.toBeUndefined();
    expect(accept).toHaveBeenCalledOnce();
    expect(acknowledge).toHaveBeenCalledOnce();
    expect(keyStore.list()).toEqual([]);
  });

  it("持久化介质写失败时先阻止POST，当前最终路径仍保留重提入口", async () => {
    const storage = new MemoryStorage();
    storage.setItem = () => { throw new Error("quota exceeded"); };
    const keyStore = new RecordingSubmissionKeyStore(storage, () => "recording:key");
    const api = { submitJob: vi.fn() };
    let failure: unknown;
    try {
      const submission = keyStore.prepare("/recordings/safe.m4a", "录音结果");
      await submitFinalizedRecording(
        submission.finalPath,
        api,
        submission.idempotencyKey,
      );
    } catch (error) {
      failure = error;
    }
    expect(api.submitJob).not.toHaveBeenCalled();
    const transition = transitionRecordingSubmissionFailure(
      {
        ...recordingState(),
        phase: "submitting",
        final_path: "/recordings/safe.m4a",
        recoverable_paths: ["/recordings/safe.m4a"],
      },
      failure,
      { ignoreRecordingEvents: true, submissionFailureFinalPath: null },
    );
    expect(transition.snapshot).toMatchObject({
      phase: "failed",
      final_path: "/recordings/safe.m4a",
      recoverable_paths: ["/recordings/safe.m4a"],
    });
  });

  it("持久化索引使用完整路径，不同目录的同名文件互不冲突", () => {
    const storage = new MemoryStorage();
    const keys = ["recording:first", "recording:second"];
    const keyStore = new RecordingSubmissionKeyStore(storage, () => keys.shift()!);
    const first = keyStore.prepare("/recordings/a/meeting.m4a", "第一段");
    const second = keyStore.prepare("/recordings/b/meeting.m4a", "第二段");
    expect(first.idempotencyKey).not.toBe(second.idempotencyKey);
    expect(keyStore.list()).toEqual([first, second]);
  });

  it("读取存储异常时模块初始化不崩，并可在当前会话继续建立提交", () => {
    const storage = new MemoryStorage();
    storage.getItem = () => { throw new Error("storage denied"); };
    const keyStore = new RecordingSubmissionKeyStore(
      storage,
      () => "recording:memory-session",
    );
    expect(keyStore.list()).toEqual([]);
    expect(keyStore.prepare("/recordings/memory.m4a", "录音结果")).toMatchObject({
      idempotencyKey: "recording:memory-session",
    });
  });

  it("清理存储异常不回滚已接纳任务，当前会话不会再次显示pending", async () => {
    const storage = new MemoryStorage();
    const keyStore = new RecordingSubmissionKeyStore(storage, () => "recording:key");
    keyStore.prepare("/recordings/accepted.m4a", "录音结果");
    storage.removeItem = () => { throw new Error("remove denied"); };
    const effect = vi.fn();
    await expect(completeRecordingAcceptance(
      { jobId: "job-one", audioPath: "/recordings/accepted.m4a" },
      keyStore,
      effect,
    )).resolves.toBeUndefined();
    expect(effect).toHaveBeenCalledOnce();
    expect(keyStore.list()).toEqual([]);
  });

  it("接纳一条后更新剩余清单写失败，也不回滚任务接纳", async () => {
    const storage = new MemoryStorage();
    const keys = ["recording:first", "recording:second"];
    const keyStore = new RecordingSubmissionKeyStore(
      storage,
      () => keys.shift()!,
    );
    keyStore.prepare("/recordings/first.m4a", "第一段");
    const second = keyStore.prepare("/recordings/second.m4a", "第二段");
    storage.setItem = () => { throw new Error("write denied"); };
    const effect = vi.fn();
    await expect(completeRecordingAcceptance(
      { jobId: "job-first", audioPath: "/recordings/first.m4a" },
      keyStore,
      effect,
    )).resolves.toBeUndefined();
    expect(effect).toHaveBeenCalledOnce();
    expect(keyStore.list()).toEqual([second]);
  });

  it("接纳副作用失败时保留key，重试接回原job且最终只接纳一次", async () => {
    const storage = new MemoryStorage();
    const keyStore = new RecordingSubmissionKeyStore(
      storage,
      () => "recording:accept-retry",
    );
    const pending = keyStore.prepare("/recordings/accept.m4a", "录音结果");
    const api = { submitJob: vi.fn().mockResolvedValue("job-existing") };
    const registry = new RecordingSubmissionRegistry();
    const failedEffect = vi.fn(() => { throw new Error("subscribe failed"); });

    await expect(registry.submitAndAccept(
      pending.finalPath,
      api,
      (result) => completeRecordingAcceptance(result, keyStore, failedEffect),
      pending.idempotencyKey,
    )).rejects.toThrow("subscribe failed");
    expect(keyStore.list()).toEqual([pending]);

    const successfulEffect = vi.fn();
    await registry.submitAndAccept(
      pending.finalPath,
      api,
      (result) => completeRecordingAcceptance(result, keyStore, successfulEffect),
      pending.idempotencyKey,
    );
    expect(api.submitJob).toHaveBeenCalledTimes(2);
    expect(api.submitJob).toHaveBeenNthCalledWith(
      1, pending.finalPath, undefined, pending.idempotencyKey,
    );
    expect(api.submitJob).toHaveBeenNthCalledWith(
      2, pending.finalPath, undefined, pending.idempotencyKey,
    );
    expect(successfulEffect).toHaveBeenCalledOnce();
    expect(keyStore.list()).toEqual([]);
  });

  it("创建与拖入音频一致的排队任务并避免重复插入", () => {
    const job = createRecordingJob(
      { jobId: "job-recorded", audioPath: "/recordings/meeting.m4a" },
      1_723_438_800,
    );
    expect(job).toEqual({
      id: "job-recorded",
      status: "queued",
      progress: 0,
      error: null,
      audio_path: "/recordings/meeting.m4a",
      created_at: 1_723_438_800,
    });
    expect(prependRecordingJob([job], job)).toEqual([job]);
  });

  it("相同jobId已有进度时保留全部状态、顺序和对象", () => {
    const existing = {
      id: "job-existing",
      status: "transcribing",
      progress: 0.68,
      error: "一次可恢复警告",
      audio_path: "/recordings/original.m4a",
      created_at: 100,
    };
    const other = { ...existing, id: "job-newer", created_at: 200 };
    const jobs = [other, existing];

    const accepted = acceptRecordingJobById(
      jobs,
      { jobId: existing.id, audioPath: "/recordings/retry.m4a" },
      999,
    );

    expect(accepted.inserted).toBe(false);
    expect(accepted.jobs).toBe(jobs);
    expect(accepted.job).toBe(existing);
    expect(accepted.jobs).toEqual([other, existing]);
  });

  it("相同jobId已完成时不重置为queued且不重新订阅", () => {
    const completed = {
      id: "job-done",
      status: "done",
      progress: 1,
      error: null,
      audio_path: "/recordings/done.m4a",
      created_at: 321,
    };
    const accepted = acceptRecordingJobById(
      [completed],
      { jobId: completed.id, audioPath: "/recordings/retry.m4a" },
      999,
    );

    expect(accepted.job).toBe(completed);
    expect(accepted.jobs).toEqual([completed]);
    expect(shouldSubscribeRecordingJob(completed, new Set())).toBe(false);
  });

  it("已有非终态任务未订阅时可补订阅，watching中不重复", () => {
    const job = {
      id: "job-running",
      status: "diarizing",
      progress: 0.8,
      error: null,
      audio_path: "/recordings/running.m4a",
      created_at: 123,
    };

    expect(shouldSubscribeRecordingJob(job, new Set())).toBe(true);
    expect(shouldSubscribeRecordingJob(job, new Set([job.id]))).toBe(false);
    expect(shouldSubscribeRecordingJob(
      { ...job, status: "failed" }, new Set(),
    )).toBe(false);
  });

  it("忽略提交期间或提交结束后晚到的快照", () => {
    const current = {
      ...recordingState(),
      phase: "submitting" as const,
      final_path: "/recordings/meeting.m4a",
    };
    const lateReady = {
      ...recordingState(),
      phase: "ready" as const,
      final_path: "/recordings/meeting.m4a",
    };
    expect(mergeBackendRecordingSnapshot(current, lateReady)).toBe(current);
    expect(
      mergeBackendRecordingSnapshot(recordingState(), lateReady, true),
    ).toEqual(recordingState());
  });

  it("本地提交失败后拒绝该轮晚到状态，新录音后恢复接收", () => {
    const failed = {
      ...recordingState(),
      phase: "failed" as const,
      final_path: "/recordings/reloaded.m4a",
      recoverable_paths: ["/recordings/reloaded.m4a"],
      error: "提交失败",
    };
    for (const phase of [
      "ready",
      "mixing",
      "stopping",
      "recording",
    ] as const) {
      const incoming = {
        ...recordingState(),
        phase,
        final_path: phase === "ready" ? "/recordings/reloaded.m4a" : null,
      };
      expect(
        mergeBackendRecordingSnapshot(failed, incoming, {
          submissionFailureTerminal: true,
        }),
      ).toBe(failed);
    }

    const starting = { ...recordingState(), phase: "starting" as const };
    expect(
      mergeBackendRecordingSnapshot(failed, starting, {
        submissionFailureTerminal: false,
      }),
    ).toBe(starting);
  });

  it("重提期间持续保护最终文件，成功后稳定清理保护和pending", () => {
    const finalPath = "/recordings/retry-cycle.m4a";
    let current: RecordingSnapshot = {
      ...recordingState(),
      phase: "failed",
      final_path: finalPath,
      recoverable_paths: [finalPath],
      error: "提交失败",
    };
    const pending: PendingRecordingSubmission[] = [{
      key: `path:${finalPath}`,
      label: "录音结果",
      finalPath,
      busy: true,
      error: null,
    }];
    current = { ...current, phase: "submitting", error: null };

    for (const phase of ["mixing", "recording", "ready"] as const) {
      const incoming: RecordingSnapshot = {
        ...recordingState(),
        phase,
        final_path: phase === "ready" ? finalPath : null,
      };
      const merged = mergeBackendRecordingSnapshot(current, incoming, {
        submissionFailureTerminal: true,
        submissionFailureFinalPath: finalPath,
      });
      expect(merged).toBe(current);
      current = merged;
    }

    // 接纳成功不依赖当前快照是否仍带 final_path；保护路径才是该轮事务依据。
    const completed = completeAcceptedRecordingSubmission(
      { ...current, final_path: null },
      pending,
      { jobId: "job-retry", audioPath: finalPath },
      finalPath,
    );
    expect(completed.snapshot).toMatchObject({
      phase: "idle",
      final_path: null,
      recoverable_paths: [],
      error: null,
    });
    expect(completed.pending).toEqual([]);
    expect(completed.submissionFailureFinalPath).toBeNull();
  });

  it("重提失败后继续保护最终文件，原生fatal只更新错误", () => {
    const finalPath = "/recordings/retry-failed.m4a";
    const retained = retainFailedRecordingSubmission(
      { ...recordingState(), phase: "submitting", final_path: finalPath },
      [],
      {
        key: `path:${finalPath}`,
        label: "录音结果",
        finalPath,
        error: new Error("仍然离线"),
      },
    );
    const lateReady = {
      ...recordingState(),
      phase: "ready" as const,
      final_path: finalPath,
    };
    expect(
      mergeBackendRecordingSnapshot(retained.snapshot, lateReady, {
        submissionFailureTerminal: true,
        submissionFailureFinalPath: finalPath,
      }),
    ).toBe(retained.snapshot);

    const nativeFatal = {
      ...recordingState(),
      phase: "failed" as const,
      final_path: null,
      error: "录音设备异常",
    };
    expect(
      mergeBackendRecordingSnapshot(retained.snapshot, nativeFatal, {
        submissionFailureTerminal: true,
        submissionFailureFinalPath: finalPath,
      }),
    ).toMatchObject({
      phase: "failed",
      final_path: finalPath,
      recoverable_paths: [finalPath],
      error: "录音设备异常",
    });
    expect(retained.pending).toHaveLength(1);
  });

  it("正常停止提交失败后解除全量忽略并改由路径marker保护", () => {
    const finalPath = "/recordings/normal-stop.m4a";
    const pending: PendingRecordingSubmission[] = [{
      key: `path:${finalPath}`,
      label: "录音结果",
      finalPath,
      busy: false,
      error: "提交失败",
    }];
    const transition = transitionRecordingSubmissionFailure(
      {
        ...recordingState(),
        phase: "submitting",
        final_path: finalPath,
        recoverable_paths: [finalPath],
      },
      new RecordingSubmissionError(finalPath, new Error("offline")),
      {
        ignoreRecordingEvents: true,
        submissionFailureFinalPath: null,
      },
    );

    expect(transition.eventGate).toEqual({
      ignoreRecordingEvents: false,
      submissionFailureFinalPath: finalPath,
    });
    expect(transition.snapshot).toMatchObject({
      phase: "failed",
      final_path: finalPath,
      recoverable_paths: [finalPath],
    });

    const fatal = mergeBackendRecordingSnapshot(
      transition.snapshot,
      {
        ...recordingState(),
        phase: "failed",
        error: "编码器异常",
      },
      {
        submissionFailureTerminal: true,
        submissionFailureFinalPath:
          transition.eventGate.submissionFailureFinalPath,
      },
    );
    expect(fatal).toMatchObject({
      phase: "failed",
      error: "编码器异常",
      final_path: finalPath,
      recoverable_paths: [finalPath],
    });
    expect(pending).toHaveLength(1);

    expect(
      mergeBackendRecordingSnapshot(
        fatal,
        { ...recordingState(), phase: "mixing" },
        {
          submissionFailureTerminal: true,
          submissionFailureFinalPath:
            transition.eventGate.submissionFailureFinalPath,
        },
      ),
    ).toBe(fatal);
  });
});

describe("多段恢复录音", () => {
  const recoverables = () => [
    {
      sessionId: "one",
      startedAt: 1,
      sessionDir: "/one",
      systemTrack: "/one/system.caf",
      microphoneTrack: null,
    },
    {
      sessionId: "two",
      startedAt: 2,
      sessionDir: "/two",
      systemTrack: "/two/system.caf",
      microphoneTrack: null,
    },
  ];

  it("首条恢复失败后第二条仍可操作", () => {
    const items = makeRecoverableRecordingItems(recoverables());
    const failed = failRecoverableRecording(
      beginRecoverableRecording(items, "one"),
      "one",
      "mix failed",
    );
    expect(failed[0]).toMatchObject({ busy: false, error: "mix failed" });
    expect(beginRecoverableRecording(failed, "two", "continue")[1]).toMatchObject({
      busy: true,
      busyAction: "continue",
      error: null,
    });
  });

  it("结束并保存不会启动下一段录音", async () => {
    const order: string[] = [];
    await runRecoverableRecordingAction(
      "finish",
      async () => { order.push("finish"); },
      async () => { order.push("start"); },
    );
    expect(order).toEqual(["finish"]);
  });

  it("继续录音会先保存旧素材再启动新的一段", async () => {
    const order: string[] = [];
    await runRecoverableRecordingAction(
      "continue",
      async () => { order.push("finish"); },
      async () => { order.push("start"); },
    );
    expect(order).toEqual(["finish", "start"]);
  });

  it("旧素材保存失败时不会启动新录音", async () => {
    const startNext = vi.fn();
    await expect(runRecoverableRecordingAction(
      "continue",
      async () => { throw new Error("mix failed"); },
      startNext,
    )).rejects.toThrow("mix failed");
    expect(startNext).not.toHaveBeenCalled();
  });

  it("两条提交失败都保留独立重提入口，成功只移除对应项", () => {
    const first = upsertPendingRecordingSubmission([], {
      key: "recover:one",
      label: "10:30 的录音",
      finalPath: "/recordings/one.m4a",
      busy: false,
      error: "offline",
    });
    const both = upsertPendingRecordingSubmission(first, {
      key: "recover:two",
      label: "11:30 的录音",
      finalPath: "/recordings/two.m4a",
      busy: false,
      error: "offline",
    });
    expect(both.map((item) => item.finalPath)).toEqual([
      "/recordings/one.m4a",
      "/recordings/two.m4a",
    ]);
    expect(removePendingRecordingSubmission(both, "path:/recordings/one.m4a")).toEqual([
      expect.objectContaining({ key: "path:/recordings/two.m4a" }),
    ]);
    expect(
      completeRecoverableRecording(
        makeRecoverableRecordingItems(recoverables()),
        "one",
      ).map((item) => item.sessionId),
    ).toEqual(["two"]);
  });
});

describe("录音关闭流程", () => {
  it("录音中只停止保存，不提交，保存成功后关闭", async () => {
    const order: string[] = [];
    await runRecordingCloseFlow(
      { ...recordingState(), phase: "recording" },
      {
        stopAndSave: vi.fn(async () => { order.push("stop-save"); }),
        persistFinalPath: vi.fn(),
        waitForSnapshot: vi.fn(),
        close: vi.fn(async () => { order.push("close"); }),
      },
    );
    expect(order).toEqual(["stop-save", "close"]);
  });

  it("保存或混音中等待现有流程，不重复停止", async () => {
    const stopAndSave = vi.fn();
    const persistFinalPath = vi.fn().mockResolvedValue(undefined);
    const close = vi.fn().mockResolvedValue(undefined);
    await runRecordingCloseFlow(
      { ...recordingState(), phase: "mixing" },
      {
        stopAndSave,
        persistFinalPath,
        waitForSnapshot: vi.fn().mockResolvedValue({
          ...recordingState(),
          phase: "ready",
          final_path: "/recordings/meeting.m4a",
        }),
        close,
      },
    );
    expect(stopAndSave).not.toHaveBeenCalled();
    expect(persistFinalPath).toHaveBeenCalledWith("/recordings/meeting.m4a");
    expect(close).toHaveBeenCalledOnce();
  });

  it("后端待确认凭据校验失败时不关闭", async () => {
    const close = vi.fn();
    await expect(
      runRecordingCloseFlow(
        { ...recordingState(), phase: "mixing" },
        {
          stopAndSave: vi.fn(),
          persistFinalPath: vi.fn().mockRejectedValue(new Error("receipt write failed")),
          waitForSnapshot: vi.fn().mockResolvedValue({
            ...recordingState(),
            phase: "ready",
            final_path: "/recordings/meeting.m4a",
          }),
          close,
        },
      ),
    ).rejects.toThrow("receipt write failed");
    expect(close).not.toHaveBeenCalled();
  });

  it("请求权限时等待，进入录音后才停止", async () => {
    const stopAndSave = vi.fn().mockResolvedValue(undefined);
    await runRecordingCloseFlow(
      { ...recordingState(), phase: "requesting_permissions" },
      {
        stopAndSave,
        persistFinalPath: vi.fn(),
        waitForSnapshot: vi.fn().mockResolvedValue({
          ...recordingState(),
          phase: "starting",
        }),
        close: vi.fn().mockResolvedValue(undefined),
      },
    );
    expect(stopAndSave).toHaveBeenCalledOnce();
  });

  it("保存失败不关闭，空闲异常关闭事件直接安全关闭", async () => {
    const close = vi.fn().mockResolvedValue(undefined);
    await expect(
      runRecordingCloseFlow(
        { ...recordingState(), phase: "stopping" },
        {
          stopAndSave: vi.fn(),
          persistFinalPath: vi.fn(),
          waitForSnapshot: vi.fn().mockResolvedValue({
            ...recordingState(),
            phase: "failed",
            error: "保存失败",
          }),
          close,
        },
      ),
    ).rejects.toThrow("保存失败");
    expect(close).not.toHaveBeenCalled();

    await runRecordingCloseFlow(recordingState(), {
      stopAndSave: vi.fn(),
      persistFinalPath: vi.fn(),
      waitForSnapshot: vi.fn(),
      close,
    });
    expect(close).toHaveBeenCalledOnce();
  });
});

describe("录音状态发布通道", () => {
  it("本地权限失败会唤醒关闭等待且不关闭", async () => {
    const channel = new RecordingSnapshotCoordinator({
      ...recordingState(),
      phase: "requesting_permissions",
    });
    const close = vi.fn();
    const flow = runRecordingCloseFlow(channel.snapshot, {
      stopAndSave: vi.fn(),
      persistFinalPath: vi.fn(),
      waitForSnapshot: () => channel.waitForAdvance(channel.revision, 100),
      close,
    });

    channel.publishLocal({
      ...recordingState(),
      phase: "failed",
      error: "权限未授权",
    });

    await expect(flow).rejects.toThrow("权限未授权");
    expect(close).not.toHaveBeenCalled();
  });

  it("监听失败和销毁都会reject等待者", async () => {
    const listenerFailure = new RecordingSnapshotCoordinator(recordingState());
    const failedWait = listenerFailure.waitForAdvance(0, 100);
    listenerFailure.fail(new Error("listener failed"));
    await expect(failedWait).rejects.toThrow("listener failed");
    await expect(listenerFailure.waitForAdvance(0, 100)).rejects.toThrow(
      "listener failed",
    );

    const destroyed = new RecordingSnapshotCoordinator(recordingState());
    const destroyedWait = destroyed.waitForAdvance(0, 100);
    destroyed.cancel("页面已销毁");
    await expect(destroyedWait).rejects.toThrow("页面已销毁");
  });

  it("无事件时超时失败且不会产生新快照", async () => {
    const channel = new RecordingSnapshotCoordinator(recordingState());
    await expect(channel.waitForAdvance(0, 5)).rejects.toThrow(
      "等待录音状态更新超时",
    );
    expect(channel.revision).toBe(0);
  });

  it("关闭等待超时保持窗口", async () => {
    const channel = new RecordingSnapshotCoordinator({
      ...recordingState(),
      phase: "mixing",
    });
    const close = vi.fn();
    await expect(
      runRecordingCloseFlow(channel.snapshot, {
        stopAndSave: vi.fn(),
        persistFinalPath: vi.fn(),
        waitForSnapshot: () => channel.waitForAdvance(0, 5),
        close,
      }),
    ).rejects.toThrow("等待录音状态更新超时");
    expect(close).not.toHaveBeenCalled();
  });

  it("被merge拒绝的late Ready不会唤醒等待者", async () => {
    const current = {
      ...recordingState(),
      phase: "submitting" as const,
      final_path: "/recordings/meeting.m4a",
    };
    const channel = new RecordingSnapshotCoordinator(current);
    const wait = channel.waitForAdvance(0, 100);

    expect(
      channel.publishBackend({
        ...recordingState(),
        phase: "ready",
        final_path: "/recordings/meeting.m4a",
      }),
    ).toBe(false);
    channel.publishLocal({
      ...current,
      phase: "failed",
      error: "提交失败",
    });

    await expect(wait).resolves.toMatchObject({
      phase: "failed",
      error: "提交失败",
    });
  });
});

describe("提交和关闭单飞", () => {
  it("同一路径并发提交只调用一次API", async () => {
    let finish!: (jobId: string) => void;
    const api = {
      submitJob: vi.fn(
        () => new Promise<string>((resolve) => { finish = resolve; }),
      ),
    };
    const registry = new RecordingSubmissionRegistry();
    const first = registry.submit(
      " /recordings/meeting.m4a ", api, "recording:same",
    );
    const second = registry.submit(
      "/recordings/meeting.m4a", api, "recording:same",
    );

    expect(api.submitJob).toHaveBeenCalledOnce();
    expect(registry.get("/recordings/meeting.m4a")).toBeTruthy();
    finish("job-one");
    await expect(Promise.all([first, second])).resolves.toEqual([
      { jobId: "job-one", audioPath: "/recordings/meeting.m4a" },
      { jobId: "job-one", audioPath: "/recordings/meeting.m4a" },
    ]);
    expect(registry.get("/recordings/meeting.m4a")).toBeUndefined();
  });

  it("同一录音的恢复、重试和关闭并发只创建一个任务", async () => {
    let finish!: (jobId: string) => void;
    const api = {
      submitJob: vi.fn(
        () => new Promise<string>((resolve) => { finish = resolve; }),
      ),
    };
    const registry = new RecordingSubmissionRegistry();
    const retry = registry.submit(
      " /recordings/shared.m4a ", api, "recording:shared",
    );
    const recovery = registry.submit(
      "/recordings/shared.m4a", api, "recording:shared",
    );
    const close = registry.submit(
      "/recordings/shared.m4a", api, "recording:shared",
    );

    finish("job-shared");
    const results = await Promise.all([retry, recovery, close]);
    const jobs = results.reduce(
      (current, result) =>
        prependRecordingJob(current, createRecordingJob(result, 100)),
      [] as ReturnType<typeof createRecordingJob>[],
    );

    expect(api.submitJob).toHaveBeenCalledOnce();
    expect(jobs).toEqual([
      expect.objectContaining({
        id: "job-shared",
        audio_path: "/recordings/shared.m4a",
      }),
    ]);
  });

  it("跨入口singleflight包含页面接纳副作用", async () => {
    let finish!: (jobId: string) => void;
    const api = {
      submitJob: vi.fn(
        () => new Promise<string>((resolve) => { finish = resolve; }),
      ),
    };
    const registry = new RecordingSubmissionRegistry();
    const accept = vi.fn();
    const subscribe = vi.fn();
    const acceptOnce = async (result: {
      jobId: string;
      audioPath: string;
    }) => {
      accept(result);
      subscribe(result.jobId);
    };

    const retry = registry.submitAndAccept(
      " /recordings/shared.m4a ",
      api,
      acceptOnce,
      "recording:shared",
    );
    const recovery = registry.submitAndAccept(
      "/recordings/shared.m4a",
      api,
      acceptOnce,
      "recording:shared",
    );
    const close = registry.submitAndAccept(
      "/recordings/shared.m4a",
      api,
      acceptOnce,
      "recording:shared",
    );
    finish("job-shared");
    await Promise.all([retry, recovery, close]);

    expect(api.submitJob).toHaveBeenCalledOnce();
    expect(accept).toHaveBeenCalledOnce();
    expect(subscribe).toHaveBeenCalledOnce();

    await registry.submitAndAccept(
      "/recordings/another.m4a",
      { submitJob: vi.fn().mockResolvedValue("job-another") },
      acceptOnce,
    );
    expect(accept).toHaveBeenCalledTimes(2);
    expect(subscribe).toHaveBeenCalledTimes(2);
  });

  it("同路径并发失败只产生一个pending入口", async () => {
    const api = { submitJob: vi.fn().mockRejectedValue(new Error("offline")) };
    const registry = new RecordingSubmissionRegistry();
    let pending: PendingRecordingSubmission[] = [];
    const retain = (error: unknown) => {
      const retained = retainFailedRecordingSubmission(
        { ...recordingState(), phase: "submitting", final_path: "/a.m4a" },
        pending,
        {
          key: "session:one",
          label: "第一段",
          finalPath: "/a.m4a",
          error,
        },
      );
      pending = retained.pending;
    };
    const first = registry.submitAndAccept(
      "/a.m4a", api, vi.fn(), "recording:one",
    ).catch((error) => {
      retain(error);
      throw error;
    });
    const second = registry.submitAndAccept(
      " /a.m4a ", api, vi.fn(), "recording:one",
    ).catch((error) => {
      retain(error);
      throw error;
    });
    await Promise.allSettled([first, second]);

    expect(api.submitJob).toHaveBeenCalledOnce();
    expect(pending).toHaveLength(1);
    expect(pending[0]).toMatchObject({
      key: "path:/a.m4a",
      finalPath: "/a.m4a",
    });
  });

  it("空白拖入路径不调用API，空jobId不创建任务", async () => {
    const blankPathApi = { submitJob: vi.fn() };
    const registry = new RecordingSubmissionRegistry();
    await expect(registry.submit("   ", blankPathApi)).rejects.toThrow(
      "最终录音路径为空",
    );
    expect(blankPathApi.submitJob).not.toHaveBeenCalled();

    const emptyJobApi = { submitJob: vi.fn().mockResolvedValue("  ") };
    let jobs: ReturnType<typeof createRecordingJob>[] = [];
    try {
      const result = await registry.submit("/recordings/empty-id.m4a", emptyJobApi);
      jobs = prependRecordingJob(jobs, createRecordingJob(result));
    } catch {
      // 提交边界拒绝空 jobId，页面不会进入建任务分支。
    }
    expect(jobs).toEqual([]);
  });

  it("普通手工提交同一路径的每次操作都创建新任务", async () => {
    const api = {
      submitJob: vi.fn()
        .mockResolvedValueOnce("job-manual-1")
        .mockResolvedValueOnce("job-manual-2"),
    };
    const registry = new RecordingSubmissionRegistry();
    const [first, second] = await Promise.all([
      registry.submit("/imports/repeat.m4a", api),
      registry.submit("/imports/repeat.m4a", api),
    ]);
    expect(api.submitJob).toHaveBeenCalledTimes(2);
    expect(first.jobId).toBe("job-manual-1");
    expect(second.jobId).toBe("job-manual-2");
  });

  it("提交失败也会settle并清理registry供重试", async () => {
    const api = {
      submitJob: vi
        .fn()
        .mockRejectedValueOnce(new Error("offline"))
        .mockResolvedValueOnce("job-retry"),
    };
    const registry = new RecordingSubmissionRegistry();
    await expect(
      registry.submit("/recordings/meeting.m4a", api),
    ).rejects.toMatchObject({ finalPath: "/recordings/meeting.m4a" });
    expect(registry.get("/recordings/meeting.m4a")).toBeUndefined();
    await expect(
      registry.submit("/recordings/meeting.m4a", api),
    ).resolves.toMatchObject({ jobId: "job-retry" });
    expect(api.submitJob).toHaveBeenCalledTimes(2);
  });

  it("close在retry提交中等待同一promise且不二次提交", async () => {
    let finish!: (jobId: string) => void;
    const api = {
      submitJob: vi.fn(
        () => new Promise<string>((resolve) => { finish = resolve; }),
      ),
    };
    const path = "/recordings/meeting.m4a";
    const registry = new RecordingSubmissionRegistry();
    const channel = new RecordingSnapshotCoordinator({
      ...recordingState(),
      phase: "submitting",
      final_path: path,
    });
    const retry = registry.submit(path, api).then((result) => {
      channel.publishLocal(recordingState());
      return result;
    });
    const persistFinalPath = vi.fn();
    const close = vi.fn().mockResolvedValue(undefined);
    const flow = runRecordingCloseFlow(channel.snapshot, {
      stopAndSave: vi.fn(),
      persistFinalPath,
      waitForSnapshot: async () => {
        await registry.get(path);
        return channel.snapshot;
      },
      close,
    });

    channel.publishBackend({
      ...recordingState(),
      phase: "ready",
      final_path: path,
    });
    finish("job-retry");
    await retry;
    await flow;

    expect(api.submitJob).toHaveBeenCalledOnce();
    expect(persistFinalPath).not.toHaveBeenCalled();
    expect(close).toHaveBeenCalledOnce();
  });

  it("重载后接管混音，ready只持久化待确认录音后关闭", async () => {
    const path = "/recordings/reloaded.m4a";
    const persistFinalPath = vi.fn().mockResolvedValue(undefined);
    const close = vi.fn().mockResolvedValue(undefined);
    await runRecordingCloseFlow(
      { ...recordingState(), phase: "mixing" },
      {
        stopAndSave: vi.fn(),
        waitForSnapshot: vi.fn().mockResolvedValue({
          ...recordingState(),
          phase: "ready",
          final_path: path,
        }),
        persistFinalPath,
        close,
      },
    );
    expect(persistFinalPath).toHaveBeenCalledWith(path);
    expect(close).toHaveBeenCalledOnce();
    const api = { submitJob: vi.fn() };
    expect(api.submitJob).not.toHaveBeenCalled();
  });

  it("重复关闭复用同一流程，失败后允许重试", async () => {
    const guard = new RecordingCloseGuard();
    let finish!: () => void;
    const close = vi.fn(
      () => new Promise<void>((resolve) => { finish = resolve; }),
    );
    const first = guard.run(close);
    const second = guard.run(close);
    expect(first).toBe(second);
    expect(close).toHaveBeenCalledOnce();
    finish();
    await first;

    const failure = new Error("close failed");
    const fail = vi.fn().mockRejectedValueOnce(failure).mockResolvedValueOnce(undefined);
    await expect(guard.run(fail)).rejects.toThrow("close failed");
    await expect(guard.run(fail)).resolves.toBeUndefined();
    expect(fail).toHaveBeenCalledTimes(2);
  });
});
