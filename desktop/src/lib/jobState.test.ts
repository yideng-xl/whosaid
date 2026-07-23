import { describe, it, expect } from "vitest";
import { canEditSpeakerCount, isRenamed, parseCount } from "./jobState";

describe("jobState", () => {
  it("canEditSpeakerCount 镜像后端契约：done 恒可改；未 done 时以 total_chunks 是否已出块判定", () => {
    expect(canEditSpeakerCount("done", 5)).toBe(true);        // done 走 rediarize 草稿流程，恒可填
    expect(canEditSpeakerCount("queued", 0)).toBe(true);      // 排队中，尚未开始分离
    expect(canEditSpeakerCount("running", 0)).toBe(true);     // 分离中，发言块尚未生成，可改
    expect(canEditSpeakerCount("running", 12)).toBe(false);   // 分离已完成、逐块转写中，锁定
    expect(canEditSpeakerCount("paused", 12)).toBe(false);    // 转写阶段暂停，分离已完成，锁定
    expect(canEditSpeakerCount("failed", 0)).toBe(true);      // 分离前失败，resume 可重新分人
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
