import { describe, it, expect } from "vitest";
import { withBusy, withoutBusy } from "./ModelManager.svelte";

describe("busyIds 集合（并发下载/切换/删除互不清空）", () => {
  it("withBusy 把某个模型 id 加入忙碌集合，不影响已有 id", () => {
    const ids = withBusy(new Set(), "model-a");
    expect(ids.has("model-a")).toBe(true);
    expect(ids.size).toBe(1);
  });

  it("withoutBusy 只移除指定 id，保留其他仍在忙碌的 id", () => {
    let ids = new Set<string>();
    ids = withBusy(ids, "model-a");
    ids = withBusy(ids, "model-b");
    ids = withoutBusy(ids, "model-a");
    expect(ids.has("model-a")).toBe(false);
    expect(ids.has("model-b")).toBe(true);
    expect(ids.size).toBe(1);
  });

  it("模拟并发下载两个模型：后发的不会清掉先发的忙碌态（复现 busyId 单值的 bug 场景）", () => {
    let ids = new Set<string>();
    // model-a 开始下载
    ids = withBusy(ids, "model-a");
    expect(ids.has("model-a")).toBe(true);
    // model-b 在 model-a 完成前也开始下载
    ids = withBusy(ids, "model-b");
    expect(ids.has("model-a")).toBe(true); // 若用单值 busyId，此处会被 model-b 覆盖掉
    expect(ids.has("model-b")).toBe(true);
    // model-a 先完成，从集合移除
    ids = withoutBusy(ids, "model-a");
    expect(ids.has("model-a")).toBe(false);
    expect(ids.has("model-b")).toBe(true); // model-b 仍在忙碌，不受影响
    // model-b 随后完成
    ids = withoutBusy(ids, "model-b");
    expect(ids.size).toBe(0);
  });

  it("withBusy/withoutBusy 不修改传入的原集合（返回新集合，配合 $state 触发响应式更新）", () => {
    const original = new Set<string>();
    const next = withBusy(original, "model-a");
    expect(original.size).toBe(0);
    expect(next).not.toBe(original);
  });

  it("withoutBusy 移除不存在的 id 时安全返回（不报错）", () => {
    const ids = withoutBusy(new Set(), "not-there");
    expect(ids.size).toBe(0);
  });
});
