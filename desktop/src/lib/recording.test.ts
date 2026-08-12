import { describe, expect, it, vi } from "vitest";
import { manageAsyncListener } from "./recording";

describe("录音事件监听生命周期", () => {
  it("组件先销毁时，晚返回的监听器会立即解除", async () => {
    let resolveListener!: (unlisten: () => void) => void;
    const registration = new Promise<() => void>((resolve) => {
      resolveListener = resolve;
    });
    const unlisten = vi.fn();
    const dispose = manageAsyncListener(registration, vi.fn());

    dispose();
    resolveListener(unlisten);
    await registration;
    await Promise.resolve();

    expect(unlisten).toHaveBeenCalledOnce();
  });

  it("监听注册失败时交给页面展示，不产生未处理拒绝", async () => {
    const onError = vi.fn();
    const registration = Promise.reject(new Error("listen failed"));

    manageAsyncListener(registration, onError);
    await Promise.resolve();
    await Promise.resolve();

    expect(onError).toHaveBeenCalledWith(expect.objectContaining({
      message: "listen failed",
    }));
  });

  it("组件销毁后忽略晚到的注册错误", async () => {
    let rejectListener!: (error: unknown) => void;
    const registration = new Promise<() => void>((_resolve, reject) => {
      rejectListener = reject;
    });
    const onError = vi.fn();
    const dispose = manageAsyncListener(registration, onError);

    dispose();
    rejectListener(new Error("late failure"));
    await registration.catch(() => undefined);
    await Promise.resolve();

    expect(onError).not.toHaveBeenCalled();
  });
});
