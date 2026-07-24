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


def _looks_garbage(text: str) -> bool:
    """判断文本是否像 whisper 在噪声/静音段的幻觉（高度重复），如 'ci D D D D D D'。
    这类段常因时长长而被选为最长段，却是无意义音，试听体验差，选段时跳过。"""
    t = "".join(text.split())  # 去所有空白
    if len(t) < 6:
        return False  # 太短不判（'行''好的'等正常短句保留）
    # 唯一字符占比过低 = 少数字符反复重复 → 幻觉
    return len(set(t)) / len(t) < 0.35


def sample_ranges(segments: list[Segment], speaker: str,
                  n: int = 3, max_sec_each: float = 4.0) -> list[tuple[float, float]]:
    """取该说话人最长的 n 段（各截前 max_sec_each 秒），按时间先后返回 [(start, dur), ...]。
    多取几段拼起来：单段常混入他人说话/杂音，难以判断，给用户多个片段更好辨认。
    优先跳过噪声幻觉段；若过滤后一段不剩则回退到不过滤（保证仍有可试听内容）。
    无该说话人返回 []。"""
    mine = [s for s in segments if s.speaker == speaker]
    if not mine:
        return []
    clean = [s for s in mine if not _looks_garbage(s.text)]
    pick = clean if clean else mine       # 全是噪声段则回退，避免试听空
    segs = [(s.end - s.start, s.start) for s in pick]
    segs.sort(reverse=True)               # 按时长降序，取最长的 n 段
    top = sorted(segs[:n], key=lambda x: x[1])   # 再按起点时间升序，听起来自然、覆盖录音不同处
    return [(start, min(dur, max_sec_each)) for dur, start in top]


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
