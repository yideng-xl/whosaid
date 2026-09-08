import { cleanup, fireEvent, render, screen } from "@testing-library/svelte";
import { tick } from "svelte";
import { afterEach, describe, expect, it, vi } from "vitest";
import Overlay from "../routes/recording-overlay/+page.svelte";

const api = vi.hoisted(() => ({ invoke: vi.fn().mockResolvedValue(undefined), drag: vi.fn().mockResolvedValue(undefined) }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: api.invoke, convertFileSrc: (path: string) => path }));
vi.mock("@tauri-apps/api/window", () => ({ getCurrentWindow: () => ({ startDragging: api.drag }) }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn(async () => vi.fn()) }));
vi.mock("./theme", () => ({ applyTheme: vi.fn(), resolveInitialTheme: () => "dark" }));
vi.mock("./recordingOverlay", () => ({ watchRecordingOverlay: (cb: (value: boolean) => void) => { cb(true); return vi.fn(); } }));
vi.mock("./recording", async importOriginal => {
  const original = await importOriginal<typeof import("./recording")>();
  const snapshot = { phase: "recording", elapsed_seconds: 12, system_audio: "active", microphone: "active", final_path: null, recoverable_paths: [], error: null };
  return { ...original, getRecordingState: async () => snapshot, watchRecording: async (cb: (value: unknown) => void) => { cb(snapshot); return vi.fn(); } };
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("精简悬浮条", () => {
  it("只显示时间和两路波形，双击唤起主窗口", async () => {
    const view = render(Overlay);
    await tick();
    expect(screen.getByText("00:12")).toBeTruthy();
    expect(view.container.querySelectorAll("svg")).toHaveLength(2);
    expect(view.container.querySelectorAll(".caption")).toHaveLength(0);
    expect(view.container.querySelectorAll("button")).toHaveLength(0);
    await fireEvent.dblClick(screen.getByRole("button", { name: "录音波形悬浮窗，双击打开 whosaid" }));
    expect(api.invoke).toHaveBeenCalledWith("open_recording_main_window");
    expect(api.drag).not.toHaveBeenCalled();
  });
});
