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
  runRecordingCloseFlow,
  submitFinalizedRecording,
  upsertPendingRecordingSubmission,
} from "./recordingFlow";
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
    expect(removePendingRecordingSubmission(both, "recover:one")).toEqual([
      expect.objectContaining({ key: "recover:two" }),
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
