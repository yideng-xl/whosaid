"""转写稿数据模型：片段、说话人重命名、导出 txt/srt、持久化。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict


def fmt_ts(seconds: float) -> str:
    """秒 → SRT 时间戳 HH:MM:SS,mmm。"""
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: str | None = None


@dataclass
class Transcript:
    segments: list[Segment] = field(default_factory=list)
    # 原始说话人标签 → 用户改的真名，如 {"说话人A": "张三"}
    speaker_names: dict[str, str] = field(default_factory=dict)

    def rename_speaker(self, orig: str, name: str) -> None:
        self.speaker_names[orig] = name

    def display_speaker(self, seg: Segment) -> str:
        if seg.speaker is None:
            return "未知"
        return self.speaker_names.get(seg.speaker, seg.speaker)

    def to_txt(self) -> str:
        # 合并相邻同一（显示）说话人的片段，便于阅读
        lines: list[tuple[str, str]] = []
        for seg in self.segments:
            spk = self.display_speaker(seg)
            if lines and lines[-1][0] == spk:
                lines[-1] = (spk, lines[-1][1] + seg.text)
            else:
                lines.append((spk, seg.text))
        return "".join(f"{spk}：{text}\n\n" for spk, text in lines)

    def to_srt(self) -> str:
        out = []
        for i, seg in enumerate(self.segments, 1):
            spk = self.display_speaker(seg)
            out.append(
                f"{i}\n{fmt_ts(seg.start)} --> {fmt_ts(seg.end)}\n[{spk}] {seg.text}\n\n"
            )
        return "".join(out)

    def plain_text(self) -> str:
        """无说话人的纯文本预览（转写进行中用）。"""
        return "\n".join(s.text for s in self.segments)

    def to_plain_ts(self) -> str:
        """逐字稿导出：[MM:SS] 文本（≥1小时用 [HH:MM:SS]），不含人名。

        以说话人变化为分段信号，相邻同一说话人的段合并成大段。
        旧数据 speaker 全为 None 时合并为连续大段。
        """
        if not self.segments:
            return ""

        def stamp(sec: float) -> str:
            """秒 → [MM:SS] 或 [HH:MM:SS]"""
            total = int(sec)
            h, rem = divmod(total, 3600)
            m, s = divmod(rem, 60)
            return f"[{h:02d}:{m:02d}:{s:02d}]" if h else f"[{m:02d}:{s:02d}]"

        blocks: list[tuple[float, str]] = []  # (start_time, text)
        prev_key = None

        for seg in self.segments:
            key = seg.speaker
            # 如果说话人与上一段相同，则合并到上一段
            if blocks and key == prev_key:
                blocks[-1] = (blocks[-1][0], blocks[-1][1] + seg.text)
            else:
                # 说话人变化，开始新段
                blocks.append((seg.start, seg.text))
            prev_key = key

        return "".join(f"{stamp(start)} {text}\n\n" for start, text in blocks)

    def to_dict(self) -> dict:
        return {
            "segments": [asdict(s) for s in self.segments],
            "speaker_names": self.speaker_names,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Transcript":
        return cls(
            segments=[Segment(**s) for s in d.get("segments", [])],
            speaker_names=dict(d.get("speaker_names", {})),
        )
