"""说话人对齐纯逻辑单元测试。"""
from transcribe_core.transcript import Segment
from transcribe_core.backend import dedup_segments, assign_speaker, align


def test_dedup_removes_consecutive_identical():
    segs = [Segment(0, 1, "重复"), Segment(1, 2, "重复"), Segment(2, 3, "不同")]
    assert [s.text for s in dedup_segments(segs)] == ["重复", "不同"]


def test_assign_speaker_max_overlap():
    turns = [(0.0, 2.0, "SPEAKER_00"), (2.0, 5.0, "SPEAKER_01")]
    assert assign_speaker(1.5, 3.0, turns) == "SPEAKER_01"  # 与01重叠1.0 > 与00重叠0.5


def test_assign_speaker_nearest_fallback_when_no_overlap():
    turns = [(0.0, 1.0, "SPEAKER_00"), (10.0, 11.0, "SPEAKER_01")]
    assert assign_speaker(1.2, 1.4, turns) == "SPEAKER_00"  # 无重叠，取最近


def test_assign_speaker_empty_turns_returns_none():
    assert assign_speaker(0.0, 1.0, []) is None


def test_align_maps_labels_in_order():
    segs = [Segment(0, 2, "甲说"), Segment(2, 4, "乙说")]
    turns = [(0.0, 2.0, "SPEAKER_05"), (2.0, 4.0, "SPEAKER_02")]
    out = align(segs, turns)
    assert [s.speaker for s in out] == ["说话人A", "说话人B"]
