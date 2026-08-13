import { describe, expect, it } from "vitest";
import {
  labelForPhase,
  recordingState,
  reduceRecordingState,
  sourceLabel,
} from "./recordingState";

describe("录音界面状态", () => {
  it("麦克风不可用时仍保持系统声音录制", () => {
    const next = reduceRecordingState(recordingState(), {
      phase: "recording",
      microphone: "unavailable",
      system_audio: "active",
    });

    expect(next.phase).toBe("recording");
    expect(next.warning).toContain("系统声音不会中断");
  });

  it("按保存、混音、提交三个阶段给出明确文案", () => {
    expect(labelForPhase("stopping")).toBe("正在保存录音");
    expect(labelForPhase("mixing")).toBe("正在合成音轨");
    expect(labelForPhase("submitting")).toBe("已开始转写");
  });

  it("麦克风恢复后清除降级警告，并保留未更新字段", () => {
    const degraded = reduceRecordingState(recordingState(), {
      phase: "recording",
      elapsed_seconds: 18,
      system_audio: "active",
      microphone: "interrupted",
    });
    const recovered = reduceRecordingState(degraded, { microphone: "active" });

    expect(recovered.elapsed_seconds).toBe(18);
    expect(degraded.warning).toContain("正在重新连接");
    expect(recovered.warning).toBeNull();
  });

  it("设备切换时显示麦克风重新连接中", () => {
    expect(sourceLabel("interrupted")).toBe("重新连接中");
  });
});
