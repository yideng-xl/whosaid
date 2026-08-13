import { fireEvent, render, screen } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";
import RecordingPanel from "./RecordingPanel.svelte";
import type { RecordingSnapshot } from "./recording";
import type { PendingRecordingSubmission } from "./recordingFlow";

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
      screen.getByRole("button", { name: "停止并保存" }),
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
    const button = screen.getByRole("button", { name: "停止并保存" });

    await fireEvent.click(button);
    await fireEvent.click(button);

    expect(onStop).toHaveBeenCalledOnce();
    expect((button as HTMLButtonElement).disabled).toBe(true);
    finish();
  });

  it("停止请求被拒绝后重新启用停止按钮", async () => {
    const onStop = vi.fn().mockRejectedValueOnce(new Error("stop failed"));
    render(RecordingPanel, { snapshot: snapshot(), onStop });

    const button = screen.getByRole("button", {
      name: "停止并保存",
    }) as HTMLButtonElement;
    await fireEvent.click(button);
    await Promise.resolve();

    expect(onStop).toHaveBeenCalledOnce();
    expect(button.disabled).toBe(false);
    expect(button.textContent).toContain("停止并保存");
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
      screen.queryByRole("button", { name: "停止并保存" }),
    ).toBeNull();
  });

  it("系统录音权限被拒绝时说明只录声音并可打开设置", async () => {
    const onOpenSystemSettings = vi.fn();
    render(RecordingPanel, {
      snapshot: snapshot({
        phase: "failed",
        system_audio: "denied",
        error: "需要系统录音权限",
      }),
      onStop: vi.fn(),
      systemPermissionDenied: true,
      onOpenSystemSettings,
    });

    expect(screen.getByText(/只录声音，不保存屏幕画面/)).toBeTruthy();
    await fireEvent.click(
      screen.getByRole("button", { name: "打开系统设置" }),
    );
    expect(onOpenSystemSettings).toHaveBeenCalledOnce();
  });

  it("最终文件提交失败时保留播放器和确认按钮供重试", async () => {
    let finish!: () => void;
    const onConfirmTranscription = vi.fn(
      () => new Promise<void>((resolve) => (finish = resolve)),
    );
    const pending = {
      key: "path:/recordings/meeting.m4a",
      label: "录音结果",
      finalPath: "/recordings/meeting.m4a",
      idempotencyKey: "recording:stable",
      busy: false,
      error: "录音已保存，但提交转写失败",
    };
    render(RecordingPanel, {
      snapshot: snapshot({
        phase: "failed",
        final_path: "/recordings/meeting.m4a",
        recoverable_paths: ["/recordings/meeting.m4a"],
        error: "录音已保存，但提交转写失败",
      }),
      onStop: vi.fn(),
      pendingRecordings: [pending],
      onConfirmTranscription,
      toAudioSrc: (path: string) => `asset://${path}`,
    });

    expect(screen.getByLabelText("试听录音结果 meeting.m4a")).toBeTruthy();
    const button = screen.getByRole("button", {
      name: "确认录音结果 meeting.m4a无误，开始转写",
    });
    await fireEvent.click(button);
    await fireEvent.click(button);

    expect(onConfirmTranscription).toHaveBeenCalledOnce();
    expect(onConfirmTranscription).toHaveBeenCalledWith(pending);
    expect((button as HTMLButtonElement).disabled).toBe(true);
    finish();
  });

  it("逐条展示可播放录音，只有确认按钮才请求转写且防止双击", async () => {
    let finish!: () => void;
    const onConfirmTranscription = vi.fn(
      () => new Promise<void>((resolve) => (finish = resolve)),
    );
    const pendingRecordings: PendingRecordingSubmission[] = [
      {
        key: "path:/recordings/one.m4a",
        label: "one.m4a",
        finalPath: "/recordings/one.m4a",
        idempotencyKey: "recording:one",
        busy: false,
        error: null,
      },
      {
        key: "path:/recordings/two.m4a",
        label: "第二段录音",
        finalPath: "/recordings/two.m4a",
        idempotencyKey: "recording:two",
        busy: false,
        error: null,
      },
    ];
    render(RecordingPanel, {
      snapshot: snapshot({ phase: "ready", final_path: "/recordings/one.m4a" }),
      onStop: vi.fn(),
      pendingRecordings,
      onConfirmTranscription,
      toAudioSrc: (path: string) => `asset://${path}`,
    });

    const players = screen.getAllByLabelText(/试听/);
    expect(players).toHaveLength(2);
    expect(players[0].getAttribute("src")).toBe("asset:///recordings/one.m4a");
    expect(screen.getAllByText("one.m4a")).toHaveLength(1);
    expect(screen.getByText("/recordings/two.m4a")).toBeTruthy();
    const buttons = screen.getAllByRole("button", {
      name: /确认.+无误，开始转写/,
    });
    await fireEvent.click(buttons[0]);
    await fireEvent.click(buttons[0]);
    expect(onConfirmTranscription).toHaveBeenCalledOnce();
    expect(onConfirmTranscription).toHaveBeenCalledWith(pendingRecordings[0]);
    expect((buttons[0] as HTMLButtonElement).disabled).toBe(true);
    finish();
  });

  it("空路径不展示播放器", () => {
    render(RecordingPanel, {
      snapshot: snapshot({ phase: "ready" }),
      onStop: vi.fn(),
      pendingRecordings: [{
        key: "blank",
        label: "空录音",
        finalPath: "   ",
        busy: false,
        error: null,
      }],
      onConfirmTranscription: vi.fn(),
    });
    expect(screen.queryByLabelText(/试听/)).toBeNull();
  });
});
