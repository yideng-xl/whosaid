import { describe, expect, it, vi } from "vitest";
import {
  beginRecoverableRecording,
  completeRecoverableRecording,
  createRecordingJob,
  failRecoverableRecording,
  finalizeAndSubmit,
  makeRecoverableRecordingItems,
  mergeBackendRecordingSnapshot,
  prependRecordingJob,
  removePendingRecordingSubmission,
  RecordingCloseGuard,
  RecordingSnapshotCoordinator,
  RecordingSubmissionRegistry,
  retainFailedRecordingSubmission,
  runRecordingCloseFlow,
  submitFinalizedRecording,
  upsertPendingRecordingSubmission,
  type PendingRecordingSubmission,
} from "./recordingFlow";
import type { RecordingSnapshot } from "./recording";
import { recordingState } from "./recordingState";

describe("录音结束后的自动提交", () => {
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
    expect(beginRecoverableRecording(failed, "two")[1]).toMatchObject({
      busy: true,
      error: null,
    });
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
  it("录音中只停止一次并在提交成功后关闭", async () => {
    const order: string[] = [];
    await runRecordingCloseFlow(
      { ...recordingState(), phase: "recording" },
      {
        stopAndSubmit: vi.fn(async () => { order.push("stop-submit"); }),
        submitFinalPath: vi.fn(),
        waitForSnapshot: vi.fn(),
        close: vi.fn(async () => { order.push("close"); }),
      },
    );
    expect(order).toEqual(["stop-submit", "close"]);
  });

  it("保存或混音中等待现有流程，不重复停止", async () => {
    const stopAndSubmit = vi.fn();
    const submitFinalPath = vi.fn().mockResolvedValue(undefined);
    const close = vi.fn().mockResolvedValue(undefined);
    await runRecordingCloseFlow(
      { ...recordingState(), phase: "mixing" },
      {
        stopAndSubmit,
        submitFinalPath,
        waitForSnapshot: vi.fn().mockResolvedValue({
          ...recordingState(),
          phase: "ready",
          final_path: "/recordings/meeting.m4a",
        }),
        close,
      },
    );
    expect(stopAndSubmit).not.toHaveBeenCalled();
    expect(submitFinalPath).toHaveBeenCalledWith("/recordings/meeting.m4a");
    expect(close).toHaveBeenCalledOnce();
  });

  it("请求权限时等待，进入录音后才停止", async () => {
    const stopAndSubmit = vi.fn().mockResolvedValue(undefined);
    await runRecordingCloseFlow(
      { ...recordingState(), phase: "requesting_permissions" },
      {
        stopAndSubmit,
        submitFinalPath: vi.fn(),
        waitForSnapshot: vi.fn().mockResolvedValue({
          ...recordingState(),
          phase: "starting",
        }),
        close: vi.fn().mockResolvedValue(undefined),
      },
    );
    expect(stopAndSubmit).toHaveBeenCalledOnce();
  });

  it("保存失败不关闭，空闲异常关闭事件直接安全关闭", async () => {
    const close = vi.fn().mockResolvedValue(undefined);
    await expect(
      runRecordingCloseFlow(
        { ...recordingState(), phase: "stopping" },
        {
          stopAndSubmit: vi.fn(),
          submitFinalPath: vi.fn(),
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
      stopAndSubmit: vi.fn(),
      submitFinalPath: vi.fn(),
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
      stopAndSubmit: vi.fn(),
      submitFinalPath: vi.fn(),
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
        stopAndSubmit: vi.fn(),
        submitFinalPath: vi.fn(),
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
    const first = registry.submit(" /recordings/meeting.m4a ", api);
    const second = registry.submit("/recordings/meeting.m4a", api);

    expect(api.submitJob).toHaveBeenCalledOnce();
    expect(registry.get("/recordings/meeting.m4a")).toBeTruthy();
    finish("job-one");
    await expect(Promise.all([first, second])).resolves.toEqual([
      { jobId: "job-one", audioPath: "/recordings/meeting.m4a" },
      { jobId: "job-one", audioPath: "/recordings/meeting.m4a" },
    ]);
    expect(registry.get("/recordings/meeting.m4a")).toBeUndefined();
  });

  it("拖入、恢复和关闭同一路径并发只创建一个任务", async () => {
    let finish!: (jobId: string) => void;
    const api = {
      submitJob: vi.fn(
        () => new Promise<string>((resolve) => { finish = resolve; }),
      ),
    };
    const registry = new RecordingSubmissionRegistry();
    const drag = registry.submit(" /recordings/shared.m4a ", api);
    const recovery = registry.submit("/recordings/shared.m4a", api);
    const close = registry.submit("/recordings/shared.m4a", api);

    finish("job-shared");
    const results = await Promise.all([drag, recovery, close]);
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

    const drag = registry.submitAndAccept(
      " /recordings/shared.m4a ",
      api,
      acceptOnce,
    );
    const recovery = registry.submitAndAccept(
      "/recordings/shared.m4a",
      api,
      acceptOnce,
    );
    const close = registry.submitAndAccept(
      "/recordings/shared.m4a",
      api,
      acceptOnce,
    );
    finish("job-shared");
    await Promise.all([drag, recovery, close]);

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
    const first = registry.submitAndAccept("/a.m4a", api, vi.fn()).catch((error) => {
      retain(error);
      throw error;
    });
    const second = registry.submitAndAccept(" /a.m4a ", api, vi.fn()).catch((error) => {
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
    const submitFinalPath = vi.fn();
    const close = vi.fn().mockResolvedValue(undefined);
    const flow = runRecordingCloseFlow(channel.snapshot, {
      stopAndSubmit: vi.fn(),
      submitFinalPath,
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
    expect(submitFinalPath).not.toHaveBeenCalled();
    expect(close).toHaveBeenCalledOnce();
  });

  it("重载后接管混音，ready提交失败会保留重提入口并阻止关闭", async () => {
    const path = "/recordings/reloaded.m4a";
    const api = { submitJob: vi.fn().mockRejectedValue(new Error("offline")) };
    const registry = new RecordingSubmissionRegistry();
    let snapshot: RecordingSnapshot = {
      ...recordingState(),
      phase: "mixing",
    };
    let pending: PendingRecordingSubmission[] = [];
    const close = vi.fn();

    await expect(
      runRecordingCloseFlow(snapshot, {
        stopAndSubmit: vi.fn(),
        waitForSnapshot: vi.fn().mockResolvedValue({
          ...recordingState(),
          phase: "ready",
          final_path: path,
        }),
        submitFinalPath: async (finalPath) => {
          snapshot = {
            ...snapshot,
            phase: "submitting",
            final_path: finalPath,
          };
          try {
            await registry.submit(finalPath, api);
          } catch (error) {
            const retained = retainFailedRecordingSubmission(
              snapshot,
              pending,
              {
                key: `final:${finalPath}`,
                label: "录音结果",
                finalPath,
                error,
              },
            );
            snapshot = retained.snapshot;
            pending = retained.pending;
            throw error;
          }
        },
        close,
      }),
    ).rejects.toMatchObject({ finalPath: path });

    expect(snapshot).toMatchObject({
      phase: "failed",
      final_path: path,
      recoverable_paths: [path],
    });
    expect(pending).toEqual([
      expect.objectContaining({
        finalPath: path,
        busy: false,
        error: expect.stringContaining("offline"),
      }),
    ]);
    expect(close).not.toHaveBeenCalled();

    const retryApi = { submitJob: vi.fn().mockResolvedValue("job-reloaded") };
    await expect(registry.submit(path, retryApi)).resolves.toMatchObject({
      jobId: "job-reloaded",
    });
    expect(retryApi.submitJob).toHaveBeenCalledOnce();
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
