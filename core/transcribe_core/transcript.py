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
