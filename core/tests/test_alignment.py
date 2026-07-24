"""说话人对齐纯逻辑单元测试。

注：assign_speaker/align 已随 diarize-first 管线改造退休并删除（说话人归属改由
diarize 直出的 turns + relabel_blocks 归一化承担，见 blocks.py），相关用例一并
移除；dedup_segments 仍被 mlx_backend.py 使用，用例保留。"""
from transcribe_core.transcript import Segment
from transcribe_core.backend import dedup_segments


def test_dedup_removes_consecutive_identical():
    segs = [Segment(0, 1, "重复"), Segment(1, 2, "重复"), Segment(2, 3, "不同")]
    assert [s.text for s in dedup_segments(segs)] == ["重复", "不同"]
