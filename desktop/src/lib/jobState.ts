// 任务状态相关的纯逻辑（可单测，供 TranscriptView 复用）
import type { Speaker } from "./api";

// 人数框是否可编辑：精确镜像后端 set_num_speakers 的契约——
// done 走 rediarize 草稿流程恒可填；未 done 时，只有分离尚未完成（total_chunks==0，
// 发言块还没生成）才可改，分离一旦完成（total_chunks>0）后端即拒绝写入，前端同步锁定。
export function canEditSpeakerCount(status: string, totalChunks: number): boolean {
  if (status === "done") return true;
  return totalChunks === 0;
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
