// 模型就绪判定的纯逻辑（可单测，供 +page.svelte 首次运行提示复用）
import type { ModelInfo } from "./api";

// 当前启用（active=true）的模型里是否存在尚未下载的——用于首次启动提示引导去填 token+下载
export function hasUndownloadedActiveModel(models: ModelInfo[]): boolean {
  return models.some((m) => m.active && !m.downloaded);
}
