import { fireEvent, render, screen } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";
import Sidebar from "./Sidebar.svelte";

describe("Sidebar 录音入口", () => {
  it("词库入口可打开管理页", async () => {
    const onOpenVocabulary = vi.fn();
    render(Sidebar, {
      jobs: [], selectedJobId: null, dragging: false,
      onSelect: vi.fn(), onOpenModels: vi.fn(), onOpenVocabulary,
      onDelete: vi.fn(),
    });
    await fireEvent.click(screen.getByRole("button", { name: "词库" }));
    expect(onOpenVocabulary).toHaveBeenCalledOnce();
  });

  it("能力确认前和不支持的平台不显示录音入口", () => {
    render(Sidebar, {
      jobs: [],
      selectedJobId: null,
      dragging: false,
      onSelect: vi.fn(),
      onOpenModels: vi.fn(),
      onDelete: vi.fn(),
      recordingAvailable: false,
    });

    expect(screen.queryByRole("button", { name: "开始录音" })).toBeNull();
  });

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
      recordingAvailable: true,
      recordingResultPending: true,
    });

    const entry = screen.getByRole("button", {
      name: "录音已保存，等待试听确认",
    });
    expect((entry as HTMLButtonElement).disabled).toBe(false);
    expect(entry.textContent).toContain("录音待确认");

    await fireEvent.click(entry);
    expect(onStartRecording).toHaveBeenCalledOnce();
  });
});
