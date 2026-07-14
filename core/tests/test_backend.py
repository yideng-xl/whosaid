"""后端纯逻辑函数测试：对齐、去重、试听选段。"""
from transcribe_core.backend import sample_range
from transcribe_core.transcript import Segment


def test_sample_range_picks_longest_and_caps():
    segs = [
        Segment(0, 2, "a", "说话人A"),
        Segment(2, 12, "b", "说话人A"),   # 最长(10s)，起点 2
        Segment(12, 13, "c", "说话人B"),
    ]
    assert sample_range(segs, "说话人A", max_sec=6.0) == (2.0, 6.0)   # 截到 6s


def test_sample_range_shorter_than_cap():
    segs = [Segment(5, 8, "x", "说话人B")]
    assert sample_range(segs, "说话人B", max_sec=6.0) == (5.0, 3.0)


def test_sample_range_missing_speaker():
    segs = [Segment(0, 1, "x", "说话人A")]
    assert sample_range(segs, "说话人Z") is None
