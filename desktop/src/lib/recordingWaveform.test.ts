import { cleanup, fireEvent, render, screen } from "@testing-library/svelte";
import { tick } from "svelte";
import { afterEach, describe, expect, it, vi } from "vitest";
import RecordingWaveform from "./RecordingWaveform.svelte";

const events = vi.hoisted(() => ({ receive: (_event: { payload: unknown }) => {}, unlisten: vi.fn() }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn(async (_name, receive) => {
  events.receive = receive;
  return events.unlisten;
}) }));

afterEach(() => { cleanup(); vi.useRealTimers(); vi.clearAllMocks(); });

describe("实时波形组件", () => {
  it("真实事件更新波形；暂停只冻结图形，断流和停止清空", async () => {
    vi.useFakeTimers();
    const view = render(RecordingWaveform, { source: "microphone", label: "麦克风", active: true });
    await tick();
    events.receive({ payload: { source: "microphone", peak: 0.5, sampledAt: Date.now() } });
    await vi.advanceTimersByTimeAsync(100);
    expect(screen.getByText("-6 dBFS")).toBeTruthy();
    const bars = () => Array.from(view.container.querySelectorAll(".sample")).map(line => line.getAttribute("y1"));
    expect(bars().at(-1)).not.toBe("26");
    await fireEvent.click(screen.getByRole("button", { name: "暂停麦克风波形显示" }));
    const frozen = bars();
    events.receive({ payload: { source: "microphone", peak: 0.1, sampledAt: Date.now() } });
    await vi.advanceTimersByTimeAsync(100);
    expect(screen.getByText("-20 dBFS")).toBeTruthy();
    expect(bars()).toEqual(frozen);
    await vi.advanceTimersByTimeAsync(600);
    expect(screen.getByText("暂无音频数据")).toBeTruthy();
    expect(bars().every(value => value === "26")).toBe(true);
    await view.rerender({ source: "microphone", label: "麦克风", active: false });
    expect(screen.getByText("未采集")).toBeTruthy();
    view.unmount();
    expect(events.unlisten).toHaveBeenCalledOnce();
  });
});
