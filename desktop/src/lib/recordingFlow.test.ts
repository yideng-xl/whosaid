import { describe, expect, it, vi } from "vitest";
import {
  createRecordingJob,
  finalizeAndSubmit,
  mergeBackendRecordingSnapshot,
  prependRecordingJob,
  submitFinalizedRecording,
} from "./recordingFlow";
import { recordingState } from "./recordingState";

describe("录音结束后的自动提交", () => {
  it("提交最终文件并返回新任务", async () => {
    const api = { submitJob: vi.fn().mockResolvedValue("job-recorded") };

    const result = await finalizeAndSubmit(
      async () => ({
        final_path: "/recordings/2026-08-12_10-30-15.m4a",
      }),
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
    const stopOk = async () => ({ final_path: "/recordings/meeting.m4a" });
    const api = {
      submitJob: vi.fn().mockRejectedValue(new Error("offline")),
    };

    await expect(finalizeAndSubmit(stopOk, api)).rejects.toMatchObject({
      finalPath: "/recordings/meeting.m4a",
    });
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
    expect(api.submitJob).toHaveBeenCalledWith("/recordings/meeting.m4a");
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

  it("忽略提交期间晚到的同一文件 ready 快照", () => {
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
      mergeBackendRecordingSnapshot(current, {
        ...lateReady,
        final_path: "/recordings/another.m4a",
      }),
    ).not.toBe(current);
  });

  it("任务已经接收后忽略晚到的后端录音快照", () => {
    const current = recordingState();
    const staleMixing = {
      ...recordingState(),
      phase: "mixing" as const,
    };

    expect(mergeBackendRecordingSnapshot(current, staleMixing, true)).toBe(
      current,
    );
  });
});
