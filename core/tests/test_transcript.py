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
