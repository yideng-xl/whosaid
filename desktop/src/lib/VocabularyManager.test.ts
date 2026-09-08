import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";
import VocabularyManager, { parseTerms } from "./VocabularyManager.svelte";

const names = {
  id: "names", name: "姓名", scope: "general" as const,
  terms: ["赵甲", "张三"], created_at: 1, updated_at: 1,
};
const product = {
  id: "jiguan", name: "产品甲", scope: "specialized" as const,
  terms: ["示例词190", "示例追溯"], created_at: 1, updated_at: 1,
};

describe("VocabularyManager", () => {
  it("支持逗号、顿号和换行，并自动去空去重", () => {
    expect(parseTerms("赵甲，张三、赵甲\n 李四 ")).toEqual(["赵甲", "张三", "李四"]);
  });

  it("按通用和专用分组展示命名词库", async () => {
    const api = { listVocabulary: vi.fn().mockResolvedValue([names, product]) };
    render(VocabularyManager, { api: api as never, onClose: vi.fn() });

    expect(await screen.findByRole("button", { name: "编辑词库 姓名" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "编辑词库 产品甲" })).toBeTruthy();
    expect(screen.getByText("通用 · 2 个词")).toBeTruthy();
    expect(screen.getByText("专用 · 2 个词")).toBeTruthy();
  });

  it("新建专用词库并按分隔符保存内容", async () => {
    const created = {
      id: "wangguan", name: "产品乙", scope: "specialized" as const,
      terms: ["示例探测", "示例词46"], created_at: 2, updated_at: 2,
    };
    const api = {
      listVocabulary: vi.fn().mockResolvedValue([]),
      addVocabulary: vi.fn().mockResolvedValue(created),
    };
    render(VocabularyManager, { api: api as never, onClose: vi.fn() });
    await screen.findByText("还没有词库");
    await fireEvent.click(screen.getByRole("button", { name: "新建第一个词库" }));
    await fireEvent.input(screen.getByLabelText("词库名称"), { target: { value: "产品乙" } });
    await fireEvent.input(screen.getByLabelText(/词库内容/), {
      target: { value: "示例探测，示例词46、示例探测" },
    });
    await fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(api.addVocabulary).toHaveBeenCalledWith({
      name: "产品乙", scope: "specialized", terms: ["示例探测", "示例词46"],
    }));
    expect(await screen.findByRole("button", { name: "编辑词库 产品乙" })).toBeTruthy();
  });

  it("编辑类型和内容，并二次确认删除", async () => {
    const updated = { ...product, scope: "general" as const, terms: ["示例词190"] };
    const api = {
      listVocabulary: vi.fn().mockResolvedValue([product]),
      updateVocabulary: vi.fn().mockResolvedValue(updated),
      deleteVocabulary: vi.fn().mockResolvedValue(undefined),
    };
    render(VocabularyManager, { api: api as never, onClose: vi.fn() });
    await fireEvent.click(await screen.findByRole("button", { name: "编辑词库 产品甲" }));
    await fireEvent.click(screen.getByRole("radio", { name: /通用词库/ }));
    await fireEvent.input(screen.getByLabelText(/词库内容/), { target: { value: "示例词190" } });
    await fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(api.updateVocabulary).toHaveBeenCalledWith("jiguan", {
      name: "产品甲", scope: "general", terms: ["示例词190"],
    }));

    await fireEvent.click(screen.getByRole("button", { name: "删除词库 产品甲" }));
    expect(screen.getByRole("dialog", { name: "删除词库" })).toBeTruthy();
    expect(api.deleteVocabulary).not.toHaveBeenCalled();
    await fireEvent.click(screen.getByRole("button", { name: "删除" }));
    await waitFor(() => expect(api.deleteVocabulary).toHaveBeenCalledWith("jiguan"));
  });
});
