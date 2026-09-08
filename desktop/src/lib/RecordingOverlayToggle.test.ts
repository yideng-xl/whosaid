import { cleanup, fireEvent, render, screen } from "@testing-library/svelte";
import { afterEach, describe, expect, it, vi } from "vitest";
import { tick } from "svelte";
import RecordingOverlayToggle from "./RecordingOverlayToggle.svelte";

const backend = vi.hoisted(() => ({ update: (_enabled: boolean) => {}, set: vi.fn(), dispose: vi.fn() }));
vi.mock("./recordingOverlay", () => ({
  watchRecordingOverlay: (update: (enabled: boolean) => void) => { backend.update = update; update(false); return backend.dispose; },
  setRecordingOverlayEnabled: backend.set,
}));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("悬浮窗开关", () => {
  it("默认关闭，切换后以后台状态为准，小窗关闭同步反映", async () => {
    backend.set.mockImplementation(async value => { backend.update(value); return value; });
    render(RecordingOverlayToggle);
    await tick();
    const control = screen.getByRole("switch", { name: "波形悬浮窗" });
    expect(control.getAttribute("aria-checked")).toBe("false");
    await fireEvent.click(control);
    expect(backend.set).toHaveBeenCalledWith(true);
    expect(control.getAttribute("aria-checked")).toBe("true");
    backend.update(false);
    await tick();
    expect(control.getAttribute("aria-checked")).toBe("false");
  });
  it("请求失败保持关闭并允许重试", async () => {
    backend.set.mockRejectedValue(new Error("window unavailable"));
    render(RecordingOverlayToggle);
    await tick();
    const control = screen.getByRole("switch", { name: "波形悬浮窗" });
    await fireEvent.click(control);
    expect(screen.getByText("未能切换悬浮窗，请重试")).toBeTruthy();
    expect(control.getAttribute("aria-checked")).toBe("false");
    expect((control as HTMLButtonElement).disabled).toBe(false);
  });
});
