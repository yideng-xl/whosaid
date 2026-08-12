import { fireEvent, render, screen } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";
import RecordingPanel from "./RecordingPanel.svelte";
import type { RecordingSnapshot } from "./recording";

const snapshot = (
  values: Partial<RecordingSnapshot> = {},
): RecordingSnapshot => ({
  phase: "recording",
  elapsed_seconds: 42,
  system_audio: "active",
  microphone: "active",
  final_path: null,
  recoverable_paths: [],
  error: null,
  ...values,
});

describe("RecordingPanel", () => {
  it("呈现双来源和时长，并用一个动作停止", async () => {
    const onStop = vi.fn();
    render(RecordingPanel, { snapshot: snapshot(), onStop });

    expect(screen.getByText("电脑声音")).toBeTruthy();
    expect(screen.getByText("麦克风")).toBeTruthy();
    expect(screen.getByText("00:42")).toBeTruthy();

    await fireEvent.click(
      screen.getByRole("button", { name: "停止并开始转写" }),
    );
    expect(onStop).toHaveBeenCalledOnce();
  });

  it("麦克风不可用时显示黄色降级提示，系统声音仍为录制中", () => {
    render(RecordingPanel, {
      snapshot: snapshot({ microphone: "unavailable" }),
      onStop: vi.fn(),
    });

    const warning = screen.getByRole("status");
    expect(warning.textContent).toContain("系统声音不会中断");
    expect(screen.getByText("电脑声音").closest(".source")?.textContent).toContain(
      "录制中",
    );
  });

  it("停止请求未结束时禁止重复操作", async () => {
    let finish!: () => void;
    const onStop = vi.fn(
      () => new Promise<void>((resolve) => (finish = resolve)),
    );
    render(RecordingPanel, { snapshot: snapshot(), onStop });
    const button = screen.getByRole("button", { name: "停止并开始转写" });

    await fireEvent.click(button);
    await fireEvent.click(button);

    expect(onStop).toHaveBeenCalledOnce();
    expect((button as HTMLButtonElement).disabled).toBe(true);
    finish();
  });

  it("失败时展示错误和全部可恢复文件路径", () => {
    render(RecordingPanel, {
      snapshot: snapshot({
        phase: "failed",
        error: "合成音轨失败",
        recoverable_paths: [
          "/recordings/.incomplete/one/system.caf",
          "/recordings/.incomplete/one/microphone.caf",
        ],
      }),
      onStop: vi.fn(),
    });

    expect(screen.getByText("合成音轨失败")).toBeTruthy();
    expect(
      screen.getByText("/recordings/.incomplete/one/system.caf"),
    ).toBeTruthy();
    expect(
      screen.getByText("/recordings/.incomplete/one/microphone.caf"),
    ).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "停止并开始转写" }),
    ).toBeNull();
  });
});
