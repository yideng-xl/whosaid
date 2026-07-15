// 主题：由 document.documentElement 上的 data-theme 驱动，localStorage 持久化。
// 未手动选择过时跟随系统（matchMedia），选择过则覆盖系统偏好。
export type Theme = "light" | "dark";

const KEY = "theme";

// 计算初始主题：优先读取已保存的手动选择，否则回退到系统偏好
export function resolveInitialTheme(): Theme {
  const saved = localStorage.getItem(KEY);
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

// 把主题写到 <html data-theme="..."> 上，驱动全局 CSS 规则生效
export function applyTheme(t: Theme): void {
  document.documentElement.dataset.theme = t;
}

// 持久化用户的手动选择
export function saveTheme(t: Theme): void {
  localStorage.setItem(KEY, t);
}
