import { fireEvent, render } from "@testing-library/svelte";
import { expect, test, vi } from "vitest";
import NameReplace from "./NameReplace.svelte";


test("渲染候选次数并提交用户勾选的替换映射", async () => {
  const onReplace = vi.fn().mockResolvedValue(3);
  const { getByLabelText, getByRole, getByText } = render(NameReplace, {
    candidates: [
      { term: "张山", count: 2 },
      { term: "张三", count: 1 },
    ],
    onReplace,
  });

  expect(getByText("出现 2 次")).toBeTruthy();
  await fireEvent.click(getByLabelText("选择 张山"));
  await fireEvent.input(getByLabelText("将 张山 替换为"), {
    target: { value: "张三" },
  });
  await fireEvent.click(getByRole("button", { name: "统一替换" }));

  expect(onReplace).toHaveBeenCalledWith({ 张山: "张三" });
  expect(await getByText("已替换 3 处")).toBeTruthy();
});


test("可以手动补充规则未识别的人名", async () => {
  const onReplace = vi.fn().mockResolvedValue(1);
  const { getByLabelText, getByRole } = render(NameReplace, {
    candidates: [],
    onReplace,
  });

  await fireEvent.click(getByRole("button", { name: "添加漏掉的人名" }));
  await fireEvent.input(getByLabelText("手动原词"), { target: { value: "小章" } });
  await fireEvent.input(getByLabelText("手动目标名"), { target: { value: "晓璋" } });
  await fireEvent.click(getByRole("button", { name: "统一替换" }));

  expect(onReplace).toHaveBeenCalledWith({ 小章: "晓璋" });
});
