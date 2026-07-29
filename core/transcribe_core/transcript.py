"""转写稿数据模型：片段、说话人重命名、导出 txt/srt、持久化。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
import re


_SINGLE_SURNAMES = (
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华"
    "金魏陶姜戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花"
    "方俞任袁柳鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时"
    "傅皮卞齐康伍余元卜顾孟平黄和穆萧尹姚邵汪祁毛禹狄米贝明"
    "臧计伏成戴宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄"
    "江童颜郭梅盛林钟徐邱骆高夏蔡田樊胡凌霍虞万支柯管卢莫经"
    "房裘缪解应宗丁宣邓郁单杭洪包诸左石崔吉龚程嵇邢裴陆荣翁"
    "荀羊甄曲封芮储靳汲邴糜隗侯宓蓬全班仰秋仲伊宫宁仇栾暴甘"
    "钭厉戎祖武符刘景詹束龙叶幸司韶郜黎蓟薄印宿白怀蒲邰从鄂"
    "索咸籍赖卓蔺屠蒙池乔阴胥能苍双闻莘党翟谭贡劳逄姬申扶堵"
    "冉宰郦雍郤璩桑桂濮牛寿通边扈燕冀郏浦尚农温别庄晏柴瞿阎"
    "充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国"
    "文寇广禄阙东欧利师巩聂晁勾敖融冷訾辛阚那简饶空曾毋沙乜"
    "养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公"
)
_COMPOUND_SURNAMES = ("欧阳", "司马", "上官", "诸葛", "东方", "皇甫", "尉迟", "公孙")
_SURNAME_RE = "(?:" + "|".join(_COMPOUND_SURNAMES) + f"|[{_SINGLE_SURNAMES}])"
_INTRO_NAME_RE = re.compile(
    rf"(?:我是|我叫|这位是|他叫|她叫|他是|她是|有请)"
    rf"(?P<name>{_SURNAME_RE}[\u4e00-\u9fff]{{1,2}})"
    rf"(?=老师|先生|女士|经理|主任|同学|博士|总|[，。！？；：、\s]|$)"
)
_TITLE_NAME_RE = re.compile(
    rf"(?:^|[，。！？；：、\s])"
    rf"(?P<name>{_SURNAME_RE}[\u4e00-\u9fff]{{1,2}})"
    rf"(?=老师|先生|女士|经理|主任|同学|博士|总)"
)


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

    def name_candidates(self) -> list[dict[str, int | str]]:
        """用保守规则推荐可能的人名；只推荐，不自动修改原稿。"""
        full_text = "\n".join(seg.text for seg in self.segments)
        candidates: set[str] = set()

        # 用户已经认领过的说话人显示名是最高置信候选；原始“说话人A”标签不算人名。
        # 三字及以上全名如果在正文里出现去姓称呼，也把该称呼列入候选（如许江辉→江辉）。
        for orig, display in self.speaker_names.items():
            name = display.strip()
            if name and name != orig and not name.startswith("说话人"):
                candidates.add(name)
                surname_len = next(
                    (len(s) for s in _COMPOUND_SURNAMES if name.startswith(s)),
                    1,
                )
                alias = name[surname_len:]
                if len(alias) >= 2 and full_text.count(alias) > 0:
                    candidates.add(alias)

        # 只接受“我是/我叫/有请”等身份介绍和“张老师/李主任”等明确称谓。
        # 不再扫描任意高频姓氏词：中文常用字中大量字也是姓氏，会把“终端/管理/应该”
        # 这类普通词误报成人名。规则宁可漏报，用户仍可手动补充。
        candidates.update(m.group("name") for m in _INTRO_NAME_RE.finditer(full_text))
        candidates.update(m.group("name") for m in _TITLE_NAME_RE.finditer(full_text))

        def occurrences(term: str) -> int:
            return full_text.count(term) + sum(
                display.count(term) for display in self.speaker_names.values()
            )

        counts = {term: occurrences(term) for term in candidates if term}
        # “李小明”与“李小”同频时只推荐完整写法，减少候选噪声。
        for short in list(counts):
            if any(
                len(long) > len(short)
                and long.startswith(short)
                and counts[long] == counts[short]
                for long in counts
            ):
                counts.pop(short)

        return [
            {"term": term, "count": count}
            for term, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        ]

    def replace_terms(self, mapping: dict[str, str]) -> int:
        """同时替换正文与说话人显示名，返回实际替换次数。

        每个值只扫描一次，目标文本不会再次成为其他规则的输入，避免连锁替换。
        """
        cleaned: dict[str, str] = {}
        for source, target in mapping.items():
            source, target = source.strip(), target.strip()
            if not source or not target:
                raise ValueError("原词和目标词不能为空")
            if source != target:
                cleaned[source] = target
        if not cleaned:
            return 0

        pattern = re.compile("|".join(re.escape(k) for k in sorted(cleaned, key=len, reverse=True)))
        replaced = 0

        def apply(value: str) -> str:
            nonlocal replaced

            def repl(match: re.Match[str]) -> str:
                nonlocal replaced
                replaced += 1
                return cleaned[match.group(0)]

            return pattern.sub(repl, value)

        for seg in self.segments:
            seg.text = apply(seg.text)
        for orig, display in list(self.speaker_names.items()):
            self.speaker_names[orig] = apply(display)
        return replaced

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
