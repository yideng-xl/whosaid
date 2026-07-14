"""转写切块的纯逻辑单测：切块计划与时间戳偏移。"""
from transcribe_core.chunking import plan_chunks, offset_segments
from transcribe_core.transcript import Segment


def test_plan_chunks_even():
    assert plan_chunks(240.0, 120.0) == [(0, 0.0, 120.0), (1, 120.0, 120.0)]


def test_plan_chunks_with_remainder():
    assert plan_chunks(200.0, 120.0) == [(0, 0.0, 120.0), (1, 120.0, 80.0)]


def test_plan_chunks_shorter_than_one_chunk():
    assert plan_chunks(30.0, 120.0) == [(0, 0.0, 30.0)]


def test_plan_chunks_zero_or_negative():
    assert plan_chunks(0.0) == []
    assert plan_chunks(-5.0) == []


def test_offset_segments_shifts_times():
    segs = [Segment(0.0, 2.0, "a"), Segment(2.0, 4.0, "b")]
    out = offset_segments(segs, 120.0)
    assert [(s.start, s.end, s.text) for s in out] == [(120.0, 122.0, "a"), (122.0, 124.0, "b")]
    assert segs[0].start == 0.0  # 原对象不变
