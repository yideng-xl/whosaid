"""转写切块的纯逻辑：切块计划与时间戳偏移。与音频 IO、推理框架无关，便于单测。"""
from __future__ import annotations

import math

from .transcript import Segment


def plan_chunks(duration: float, chunk_sec: float = 120.0) -> list[tuple[int, float, float]]:
    """把 [0, duration) 按 chunk_sec 切成 (index, start, dur) 列表，最后一块为余量。"""
    if duration <= 0:
        return []
    n = math.ceil(duration / chunk_sec)
    chunks: list[tuple[int, float, float]] = []
    for i in range(n):
        start = i * chunk_sec
        dur = min(chunk_sec, duration - start)
        chunks.append((i, start, dur))
    return chunks


def offset_segments(segs: list[Segment], offset: float) -> list[Segment]:
    """把每个片段的时间戳整体加上 offset（返回新列表，不改原对象）。"""
    return [Segment(s.start + offset, s.end + offset, s.text, s.speaker) for s in segs]
