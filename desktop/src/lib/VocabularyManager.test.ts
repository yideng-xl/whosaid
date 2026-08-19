import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";
import VocabularyManager, { parseAliases } from "./VocabularyManager.svelte";

describe("VocabularyManager", () => {
  it("别名支持逗号、顿号和换行，并去重", () => {
    expect(parseAliases("许雷，徐磊、许雷\n 老许 ")).toEqual(["许雷", "徐磊", "老许"]);
  });

  it("展示词库，并在添加时发送规范化别名", async () => {
    const existing = {
      id: "p1", kind: "person" as const, canonical: "许磊", aliases: ["许雷"],
      enabled: true, created_at: 1, updated_at: 1,
    };
    const created = {
      id: "t1", kind: "term" as const, canonical: "端到端探测", aliases: ["端到端弹策"],
      enabled: true, created_at: 2, updated_at: 2,
    };
    const api = {
      listVocabulary: vi.fn().mockResolvedValue([existing]),
      addVocabulary: vi.fn().mockResolvedValue(created),
      updateVocabulary: vi.fn(),
      deleteVocabulary: vi.fn(),
    };
    render(VocabularyManager, { api: api as never, onClose: vi.fn() });

    expect(await screen.findByText("许磊")).toBeTruthy();
    await fireEvent.change(screen.getByLabelText("类型"), { target: { value: "term" } });
    await fireEvent.input(screen.getByLabelText("标准写法"), { target: { value: "端到端探测" } });
    await fireEvent.input(screen.getByLabelText("常见误写或别名（可选）"), { target: { value: "端到端弹策，端到端弹策" } });
    await fireEvent.click(screen.getByRole("button", { name: "添加" }));

    await waitFor(() => expect(api.addVocabulary).toHaveBeenCalledWith({
      kind: "term", canonical: "端到端探测", aliases: ["端到端弹策"], enabled: true,
    }));
    expect(await screen.findByText("端到端探测")).toBeTruthy();
  });

  it("删除前需要二次确认", async () => {
    const entry = {
      id: "p1", kind: "person" as const, canonical: "张三", aliases: [],
      enabled: true, created_at: 1, updated_at: 1,
    };
    const api = {
      listVocabulary: vi.fn().mockResolvedValue([entry]),
      addVocabulary: vi.fn(), updateVocabulary: vi.fn(),
      deleteVocabulary: vi.fn().mockResolvedValue(undefined),
    };
    render(VocabularyManager, { api: api as never, onClose: vi.fn() });
    expect(await screen.findByText("张三")).toBeTruthy();

    await fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(api.deleteVocabulary).not.toHaveBeenCalled();
    await fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(api.deleteVocabulary).toHaveBeenCalledWith("p1"));
  });
});
