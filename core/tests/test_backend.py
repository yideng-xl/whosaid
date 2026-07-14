"""后端纯逻辑函数测试：对齐、去重、试听选段。"""
from transcribe_core.backend import sample_range, sample_ranges
from transcribe_core.transcript import Segment


def test_sample_ranges_picks_top3_in_time_order():
    segs = [
        Segment(0, 1, "a", "说话人A"),     # 1s
        Segment(10, 20, "b", "说话人A"),   # 10s 最长
        Segment(30, 36, "c", "说话人A"),   # 6s
        Segment(40, 48, "d", "说话人A"),   # 8s
        Segment(50, 51, "x", "说话人B"),
    ]
    # 取最长 3 段(10/8/6s)，按起点时间升序返回，各截前 4s
    r = sample_ranges(segs, "说话人A", n=3, max_sec_each=4.0)
    assert r == [(10.0, 4.0), (30.0, 4.0), (40.0, 4.0)]


def test_sample_ranges_fewer_than_n():
    segs = [Segment(0, 2, "a", "说话人A")]
    assert sample_ranges(segs, "说话人A", n=3) == [(0.0, 2.0)]


def test_sample_ranges_missing_speaker():
    segs = [Segment(0, 2, "a", "说话人A")]
    assert sample_ranges(segs, "说话人Z") == []


def test_sample_ranges_skips_hallucinated_segment():
    segs = [
        Segment(0, 5, "今天我们开个产品评审会", "说话人A"),      # 正常，5s
        Segment(10, 30, "D D D D D D D D D D", "说话人A"),      # 噪声幻觉，20s 最长但应跳过
    ]
    # 尽管噪声段更长，也应选正常段
    assert sample_ranges(segs, "说话人A", n=1) == [(0.0, 4.0)]


def test_sample_ranges_falls_back_when_all_garbage():
    segs = [Segment(10, 30, "D D D D D D D D D D", "说话人A")]
    # 全是噪声段则回退，仍返回内容而非空
    assert sample_ranges(segs, "说话人A", n=1) == [(10.0, 4.0)]


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
