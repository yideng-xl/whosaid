import { describe, expect, it, vi } from "vitest";
import {
  initializeDirectRecording,
  manageAsyncListener,
  recordingAudioSrc,
} from "./recording";
import pageSource from "../routes/+page.svelte?raw";

describe("直接录音平台门禁", () => {
  it("播放器通过受控转换器生成本地资源地址，空路径不转换", () => {
    const convert = vi.fn((path: string) => `asset://${path}`);
    expect(recordingAudioSrc(" /recordings/a.m4a ", convert)).toBe(
      "asset:///recordings/a.m4a",
    );
    expect(recordingAudioSrc("   ", convert)).toBe("");
    expect(convert).toHaveBeenCalledOnce();
  });

  it("不支持的平台不会执行任何录音初始化", async () => {
    const initialize = vi.fn();

    await expect(initializeDirectRecording(
      initialize,
      async () => ({ directRecording: false }),
    )).resolves.toBe(false);

    expect(initialize).not.toHaveBeenCalled();
  });

  it("能力读取失败时关闭功能且不执行任何录音初始化", async () => {
    const initialize = vi.fn();

    await expect(initializeDirectRecording(
      initialize,
      async () => { throw new Error("capability unavailable"); },
    )).resolves.toBe(false);

    expect(initialize).not.toHaveBeenCalled();
  });

  it("支持的平台才执行录音初始化", async () => {
    const initialize = vi.fn();

    await expect(initializeDirectRecording(
      initialize,
      async () => ({ directRecording: true }),
    )).resolves.toBe(true);

    expect(initialize).toHaveBeenCalledOnce();
  });

  it.each([
    null,
    undefined,
    [],
    "false",
    1,
    {},
    { directRecording: false },
    { directRecording: "true" },
    { directRecording: 1 },
  ])("异常能力值 %# 必须关闭功能", async (value) => {
    const initialize = vi.fn();

    await expect(initializeDirectRecording(
      initialize,
      async () => value as never,
    )).resolves.toBe(false);

    expect(initialize).not.toHaveBeenCalled();
  });

  it("退出确认框同时受平台能力和确认状态控制", () => {
    expect(pageSource).toContain("{#if recordingAvailable && closeRequested}");
  });
});

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
