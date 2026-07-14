"""推理后端抽象 + 与推理框架无关的纯对齐逻辑。"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .transcript import Segment

Turn = tuple[float, float, str]


def dedup_segments(segs: list[Segment]) -> list[Segment]:
    """去掉与上一段完全相同文本的连续片段（whisper 常见重复幻觉）。"""
    out: list[Segment] = []
    for s in segs:
        if out and out[-1].text == s.text:
            continue
        out.append(s)
    return out


def assign_speaker(start: float, end: float, turns: list[Turn]) -> str | None:
    """把片段分给时间重叠最大的说话人；无重叠则归最近的；空 turns 返回 None。"""
    if not turns:
        return None
    best_spk, best_overlap = None, 0.0
    for t_start, t_end, spk in turns:
        overlap = min(end, t_end) - max(start, t_start)
        if overlap > best_overlap:
            best_overlap, best_spk = overlap, spk
    if best_spk is not None:
        return best_spk
    mid = (start + end) / 2
    nearest, best_gap = None, float("inf")
    for t_start, t_end, spk in turns:
        gap = 0.0 if t_start <= mid <= t_end else min(abs(mid - t_start), abs(mid - t_end))
        if gap < best_gap:
            best_gap, nearest = gap, spk
    return nearest


def align(segments: list[Segment], turns: list[Turn]) -> list[Segment]:
    """给每个片段分配说话人，并把原始标签按出现顺序映射为 说话人A/B…。"""
    label_map: dict[str, str] = {}
    for _, _, raw in turns:
        if raw not in label_map:
            label_map[raw] = f"说话人{chr(ord('A') + len(label_map))}"
    result: list[Segment] = []
    for seg in segments:
        raw = assign_speaker(seg.start, seg.end, turns)
        spk = label_map.get(raw, raw) if raw is not None else None
        result.append(Segment(seg.start, seg.end, seg.text, spk))
    return result


def sample_range(segments: list[Segment], speaker: str, max_sec: float = 6.0):
    """找某说话人最长的一段，返回 (start, min(时长, max_sec))；无该说话人返回 None。"""
    best = None  # (dur, start)
    for s in segments:
        if s.speaker != speaker:
            continue
        dur = s.end - s.start
        if best is None or dur > best[0]:
            best = (dur, s.start)
    if best is None:
        return None
    dur, start = best
    return (start, min(dur, max_sec))


class InferenceBackend(ABC):
    """推理后端抽象。一期实现为 MlxBackend；扩展平台时新增实现即可。"""

    id: str = "abstract"

    @abstractmethod
    def transcribe(
        self, audio_path: str, language: str | None, initial_prompt: str | None
    ) -> list[Segment]:
        """返回不含说话人的转写片段（speaker=None）。"""

    @abstractmethod
    def diarize(self, audio_path: str, num_speakers: int | None) -> list[Turn]:
        """返回说话人时间段 [(start, end, raw_speaker), ...]。"""
