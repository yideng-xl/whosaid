import { describe, it, expect } from "vitest";
import { canEditSpeakerCount, isRenamed, parseCount } from "./jobState";

describe("jobState", () => {
  it("canEditSpeakerCount 正在分人时锁定", () => {
    expect(canEditSpeakerCount("running", 0.9)).toBe(false);   // diarizing
    expect(canEditSpeakerCount("running", 0.4)).toBe(true);    // 听写中
    expect(canEditSpeakerCount("queued", 0)).toBe(true);
    expect(canEditSpeakerCount("paused", 0.5)).toBe(true);
    expect(canEditSpeakerCount("failed", 0)).toBe(true);
    expect(canEditSpeakerCount("done", 1)).toBe(true);
  });

  it("isRenamed 检测是否改过真名", () => {
    expect(isRenamed([{ orig: "说话人A", name: "说话人A" }])).toBe(false);
    expect(isRenamed([{ orig: "说话人A", name: "张三" }])).toBe(true);
    expect(isRenamed([])).toBe(false);
  });

  it("parseCount 规整输入", () => {
    expect(parseCount("3")).toBe(3);
    expect(parseCount("")).toBe(null);
    expect(parseCount("0")).toBe(null);
    expect(parseCount("-2")).toBe(null);
    expect(parseCount("abc")).toBe(null);
  });
});
