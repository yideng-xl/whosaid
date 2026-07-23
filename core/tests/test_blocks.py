from transcribe_core.blocks import refine_turns


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
