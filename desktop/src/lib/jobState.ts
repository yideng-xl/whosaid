// 任务状态相关的纯逻辑（可单测，供 TranscriptView 复用）
import type { Speaker } from "./api";

// 人数框是否可编辑：正在分人（running & progress≥0.85）锁定，其余状态可编辑
export function canEditSpeakerCount(status: string, progress: number): boolean {
  if (status === "running" && progress >= 0.85) return false;
  return ["queued", "running", "paused", "failed", "done"].includes(status);
}

// 是否改过真名（决定重新分人前是否弹确认）
export function isRenamed(speakers: Speaker[]): boolean {
  return speakers.some((s) => s.name !== s.orig);
}

// 输入框字符串 → 人数值：空/非正整数一律 null（自动）
export function parseCount(v: string): number | null {
  const n = parseInt(v, 10);
  return Number.isFinite(n) && n > 0 ? n : null;
}
