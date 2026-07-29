# tests/test_transcript.py
from transcribe_core.transcript import Segment, Transcript, fmt_ts


def test_fmt_ts():
    assert fmt_ts(3661.5) == "01:01:01,500"


def test_to_txt_merges_consecutive_same_speaker():
    t = Transcript(segments=[
        Segment(0.0, 1.0, "你好", "说话人A"),
        Segment(1.0, 2.0, "在吗", "说话人A"),
        Segment(2.0, 3.0, "在的", "说话人B"),
    ])
    assert t.to_txt() == "说话人A：你好在吗\n\n说话人B：在的\n\n"


def test_rename_speaker_reflected_in_output():
    t = Transcript(segments=[Segment(0.0, 1.0, "开会了", "说话人A")])
    t.rename_speaker("说话人A", "张三")
    assert t.display_speaker(t.segments[0]) == "张三"
    assert t.to_txt() == "张三：开会了\n\n"


def test_to_srt_format():
    t = Transcript(segments=[Segment(0.0, 1.5, "测试", "说话人A")])
    assert t.to_srt() == "1\n00:00:00,000 --> 00:00:01,500\n[说话人A] 测试\n\n"


def test_roundtrip_dict():
    t = Transcript(segments=[Segment(0.0, 1.0, "hi", "说话人A")])
    t.rename_speaker("说话人A", "李四")
    t2 = Transcript.from_dict(t.to_dict())
    assert t2.to_txt() == t.to_txt()


def test_plain_text_joins_without_speaker():
    t = Transcript(segments=[Segment(0, 1, "你好"), Segment(1, 2, "在吗")])
    assert t.plain_text() == "你好\n在吗"


def test_to_plain_ts_timestamps_no_speaker_names():
    t = Transcript(segments=[
        Segment(0.0, 5.0, "大家好", speaker="说话人A"),
        Segment(5.0, 9.0, "今天开会", speaker="说话人A"),
        Segment(65.0, 70.0, "下一个议题", speaker="说话人B"),
    ], speaker_names={"说话人A": "张三"})
    out = t.to_plain_ts()
    assert "张三" not in out and "说话人A" not in out  # 不带人名
    assert out.startswith("[00:00]")
    assert "[01:05]" in out  # 65s → 01:05


def test_to_plain_ts_hours_prefix():
    t = Transcript(segments=[Segment(3661.0, 3665.0, "很久以后")])
    assert "[01:01:01]" in t.to_plain_ts()


def test_to_plain_ts_empty():
    assert Transcript(segments=[]).to_plain_ts() == ""


def test_name_candidates_include_context_and_speaker_display_name():
    t = Transcript(
        segments=[
            Segment(0, 1, "我叫张山，这位是张三。", "说话人A"),
            Segment(1, 2, "张山稍后回复，有请李小明。", "说话人B"),
            Segment(2, 3, "李小明已经收到，江辉也确认了。", "说话人A"),
        ],
        speaker_names={"说话人A": "王小华", "说话人B": "许江辉"},
    )

    got = {item["term"]: item["count"] for item in t.name_candidates()}

    assert got["张山"] == 2
    assert got["张三"] == 1
    assert got["李小明"] == 2
    assert got["王小华"] == 1
    assert got["许江辉"] == 1
    assert got["江辉"] == 2  # 已认领全名后，正文里的去姓称呼也可统一
    assert "李小" not in got  # 同频的完整三字名存在时，不重复推荐其前缀


def test_name_candidates_do_not_treat_repeated_surname_words_as_people():
    """常见词首字碰巧是姓氏时不能入选；这是实稿里候选杂项的回归用例。"""
    t = Transcript(segments=[
        Segment(
            0,
            1,
            "别别别，那个那个，终端终端，平面平面，管理管理，"
            "关地址关地址，全区全区，安全区安全区，应该应该，路线路线。",
        )
    ])

    terms = {item["term"] for item in t.name_candidates()}

    assert terms == set()


def test_replace_terms_updates_text_and_speaker_names_without_cascading():
    t = Transcript(
        segments=[
            Segment(0, 1, "张山请张三确认。", "说话人A"),
            Segment(1, 2, "张山收到。", "说话人B"),
        ],
        speaker_names={"说话人A": "张山", "说话人B": "李四"},
    )

    replaced = t.replace_terms({"张山": "张三", "张三": "张珊"})

    assert replaced == 4
    assert t.segments[0].text == "张三请张珊确认。"
    assert t.segments[1].text == "张三收到。"
    assert t.speaker_names == {"说话人A": "张三", "说话人B": "李四"}


def test_replace_terms_rejects_blank_source_or_target():
    t = Transcript(segments=[Segment(0, 1, "张山")])

    import pytest

    with pytest.raises(ValueError, match="不能为空"):
        t.replace_terms({"": "张三"})
    with pytest.raises(ValueError, match="不能为空"):
        t.replace_terms({"张山": "  "})
