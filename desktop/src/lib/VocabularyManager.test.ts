import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";
import VocabularyManager, { parseTerms } from "./VocabularyManager.svelte";

describe("VocabularyManager", () => {
  it("支持逗号、顿号和换行，并自动去空去重", () => {
    expect(parseTerms("许磊，张三、许磊\n 李四 ")).toEqual(["许磊", "张三", "李四"]);
  });

  it("加载已有三类词库并整组保存", async () => {
    const api = {
      listVocabulary: vi.fn().mockResolvedValue([
        { id: "p1", kind: "person", canonical: "许磊", aliases: [], enabled: true, created_at: 1, updated_at: 1 },
        { id: "t1", kind: "term", canonical: "端到端探测", aliases: [], enabled: true, created_at: 1, updated_at: 1 },
        { id: "o1", kind: "other", canonical: "陕西省调", aliases: [], enabled: true, created_at: 1, updated_at: 1 },
      ]),
      replaceVocabulary: vi.fn().mockImplementation(async (groups) => [
        ...groups.person.map((canonical: string, index: number) => ({ id: `p${index}`, kind: "person", canonical, aliases: [], enabled: true, created_at: 1, updated_at: 1 })),
        ...groups.term.map((canonical: string, index: number) => ({ id: `t${index}`, kind: "term", canonical, aliases: [], enabled: true, created_at: 1, updated_at: 1 })),
        ...groups.other.map((canonical: string, index: number) => ({ id: `o${index}`, kind: "other", canonical, aliases: [], enabled: true, created_at: 1, updated_at: 1 })),
      ]),
    };
    render(VocabularyManager, { api: api as never, onClose: vi.fn() });

    const person = await screen.findByLabelText(/姓名 1 个/);
    const term = screen.getByLabelText(/专用词库 1 个/);
    const other = screen.getByLabelText(/其他 1 个/);
    expect((person as HTMLTextAreaElement).value).toBe("许磊");
    expect((term as HTMLTextAreaElement).value).toBe("端到端探测");
    expect((other as HTMLTextAreaElement).value).toBe("陕西省调");

    await fireEvent.input(person, { target: { value: "许磊，张三、许磊" } });
    await fireEvent.input(term, { target: { value: "端到端探测、终端IP核查" } });
    await fireEvent.input(other, { target: { value: "陕西省调，产品部" } });
    await fireEvent.click(screen.getByRole("button", { name: "保存词库" }));

    await waitFor(() => expect(api.replaceVocabulary).toHaveBeenCalledWith({
      person: ["许磊", "张三"], term: ["端到端探测", "终端IP核查"], other: ["陕西省调", "产品部"],
    }));
    expect(await screen.findByRole("button", { name: "已保存" })).toBeTruthy();
  });

  it("清空文本框并保存即可删除该类词库", async () => {
    const api = {
      listVocabulary: vi.fn().mockResolvedValue([
        { id: "p1", kind: "person", canonical: "张三", aliases: [], enabled: true, created_at: 1, updated_at: 1 },
      ]),
      replaceVocabulary: vi.fn().mockResolvedValue([]),
    };
    render(VocabularyManager, { api: api as never, onClose: vi.fn() });
    const person = await screen.findByLabelText(/姓名 1 个/);
    await fireEvent.input(person, { target: { value: "" } });
    await fireEvent.click(screen.getByRole("button", { name: "保存词库" }));
    await waitFor(() => expect(api.replaceVocabulary).toHaveBeenCalledWith({ person: [], term: [], other: [] }));
  });
});
