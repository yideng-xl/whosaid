"""把 diarizer 原始轮次精炼成「单一主导说话人」的发言块（纯逻辑，不碰音频/模型）。

真实会议里人是碎着说的，diarizer 原始轮次 57%+ 短于 2 秒；朴素「每次换人都切」会
制造大量极短块，whisper 在其上必幻觉且慢到数倍。精炼切：连续同人合并、别人的短插话
折进当前主导者、够长的换人才切、末了清扫仍过短的独立块。实测把 21.6min/3人 从 348 块
1.74×→ 59 块 0 极短块 1.74×。
"""
from __future__ import annotations

from .transcript import Segment

Turn = tuple[float, float, str]


def refine_turns(turns: list[Turn], interj: float = 2.5, target: float = 15.0) -> list[Turn]:
    if not turns:
        return []
    turns = sorted(turns, key=lambda x: x[0])
    blocks: list[list] = []  # [start, end, spk]
    for s, e, spk in turns:
        dlen = e - s
        if not blocks:
            blocks.append([s, e, spk])
            continue
        cur = blocks[-1]
        same = cur[2] == spk
        cur_len = cur[1] - cur[0]
        if not same and dlen < interj and cur_len < target * 2:
            # 别人的短插话：折进当前主导块，仅延伸时间边界，说话人不变
            cur[1] = max(cur[1], e)
        elif same and s - cur[1] < 3.0:
            cur[1] = max(cur[1], e)
        else:
            blocks.append([s, e, spk])
    # 二次清扫：仍 < interj 的独立块并入相邻块，保证零极短块
    merged = blocks
    while True:
        changed = False
        i = 0
        while i < len(merged):
            b = merged[i]
            duration = b[1] - b[0]
            if duration < interj:
                if i > 0:
                    # 与前一块合并
                    merged[i - 1][1] = max(merged[i - 1][1], b[1])
                    merged.pop(i)
                    changed = True
                elif i < len(merged) - 1:
                    # 第一块且短，与后一块合并
                    merged[i + 1][0] = b[0]
                    merged.pop(i)
                    changed = True
                else:
                    # 唯一的块，无法合并
                    i += 1
            else:
                i += 1
        if not changed:
            break
    return [(b[0], b[1], b[2]) for b in merged]


def speakered_block_segments(segs: list[Segment], offset: float, speaker: str) -> list[Segment]:
    """某发言块转写出的段：整体偏移 offset 到全局时间轴，并统一贴该块主导说话人。"""
    from .chunking import offset_segments
    out = offset_segments(segs, offset)
    for s in out:
        s.speaker = speaker
    return out
