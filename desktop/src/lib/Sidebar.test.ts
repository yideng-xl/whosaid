import { fireEvent, render, screen } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";
import Sidebar from "./Sidebar.svelte";

describe("Sidebar 录音入口", () => {
  it("最终文件待提交时保留可返回录音结果的入口", async () => {
    const onStartRecording = vi.fn();
    render(Sidebar, {
      jobs: [],
      selectedJobId: null,
      dragging: false,
      onSelect: vi.fn(),
      onOpenModels: vi.fn(),
      onDelete: vi.fn(),
      onStartRecording,
      recordingResultPending: true,
    });

    const entry = screen.getByRole("button", {
      name: "录音已保存，等待提交转写",
    });
    expect((entry as HTMLButtonElement).disabled).toBe(false);
    expect(entry.textContent).toContain("录音待提交");

    await fireEvent.click(entry);
    expect(onStartRecording).toHaveBeenCalledOnce();
  });
});
