import { fireEvent, render, screen } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";
import VocabularyPicker, { defaultVocabularySelection } from "./VocabularyPicker.svelte";

const libraries = [
  { id: "names", name: "姓名", scope: "general" as const, terms: ["赵甲"], created_at: 1, updated_at: 1 },
  { id: "orgs", name: "组织", scope: "general" as const, terms: ["产品部"], created_at: 1, updated_at: 1 },
  { id: "jiguan", name: "产品甲", scope: "specialized" as const, terms: ["示例词190"], created_at: 1, updated_at: 1 },
];

describe("VocabularyPicker", () => {
  it("默认选中全部通用词库，不选专用词库", () => {
    expect(defaultVocabularySelection(libraries)).toEqual(["names", "orgs"]);
    expect(defaultVocabularySelection(libraries, ["jiguan", "missing"]))
      .toEqual(["jiguan"]);
  });

  it("允许按本次会议调整并提交选择", async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    render(VocabularyPicker, {
      libraries,
      audioName: "产品周例会.m4a",
      onConfirm,
      onCancel: vi.fn(),
    });
    const names = screen.getByRole("checkbox", { name: /姓名/ }) as HTMLInputElement;
    const product = screen.getByRole("checkbox", { name: /产品甲/ }) as HTMLInputElement;
    expect(names.checked).toBe(true);
    expect(product.checked).toBe(false);
    await fireEvent.click(product);
    await fireEvent.click(screen.getByRole("button", { name: "开始转写" }));
    expect(onConfirm).toHaveBeenCalledWith(["names", "orgs", "jiguan"]);
  });
});
