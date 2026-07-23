from transcribe_core.blocks import refine_turns, relabel_blocks, speakered_block_segments
from transcribe_core.transcript import Segment


def test_speakered_block_segments_offsets_and_labels():
    segs = [Segment(0.0, 2.0, "你好"), Segment(2.0, 4.0, "在的")]
    out = speakered_block_segments(segs, offset=100.0, speaker="说话人A")
    assert [s.speaker for s in out] == ["说话人A", "说话人A"]
    assert out[0].start == 100.0 and out[1].end == 104.0
    assert out[0].text == "你好"
    assert segs[0].speaker is None  # 原对象未被污染（offset_segments 返回新对象）


def test_merges_consecutive_same_speaker():
    turns = [(0.0, 5.0, "A"), (5.2, 9.0, "A")]  # 同人、间隔0.2s
    blocks = refine_turns(turns)
    assert blocks == [(0.0, 9.0, "A")]


def test_folds_short_interjection_into_dominant():
    # A 说很久，中间 B 插 1 秒背景音 → 折进 A，不单独成块
    turns = [(0.0, 10.0, "A"), (10.0, 11.0, "B"), (11.0, 20.0, "A")]
    blocks = refine_turns(turns, interj=2.5, target=15.0)
    assert len(blocks) == 1
    assert blocks[0][2] == "A"
    assert blocks[0][0] == 0.0 and blocks[0][1] == 20.0


def test_real_speaker_change_splits():
    # 两个人各说 20 秒 → 两块
    turns = [(0.0, 20.0, "A"), (20.0, 40.0, "B")]
    blocks = refine_turns(turns)
    assert [b[2] for b in blocks] == ["A", "B"]


def test_no_micro_blocks_remain():
    # 一堆 <2.5s 的碎片 → 合并后不得有 <2.5s 的独立块
    turns = [(0.0, 1.0, "A"), (1.0, 1.5, "B"), (1.5, 2.0, "A"), (2.0, 30.0, "B")]
    blocks = refine_turns(turns)
    assert all((e - s) >= 2.5 for s, e, _ in blocks)


def test_empty_input():
    assert refine_turns([]) == []


def test_relabel_blocks_maps_raw_to_friendly_by_first_appearance():
    blocks = [(0.0, 20.0, "SPEAKER_01"), (20.0, 40.0, "SPEAKER_00"), (40.0, 50.0, "SPEAKER_01")]
    out = relabel_blocks(blocks)
    # SPEAKER_01 先出现 → 说话人A；SPEAKER_00 后出现 → 说话人B；再次 SPEAKER_01 → 仍说话人A
    assert [b[2] for b in out] == ["说话人A", "说话人B", "说话人A"]
    assert [(b[0], b[1]) for b in out] == [(0.0, 20.0), (20.0, 40.0), (40.0, 50.0)]  # 时间不变


def test_relabel_blocks_empty():
    assert relabel_blocks([]) == []
