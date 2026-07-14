import os
import pytest
from transcribe_core.audio import probe_duration, extract_wav, extract_sample

CLIP = os.path.expanduser(
    "~/Workspace/develop/oneworkspace/local-ai/radio/测试短音频-8秒.m4a"
)
pytestmark = pytest.mark.skipif(not os.path.exists(CLIP), reason="缺测试音频")


@pytest.mark.slow
def test_probe_duration_positive():
    d = probe_duration(CLIP)
    assert 6.0 < d < 12.0  # 8 秒左右


@pytest.mark.slow
def test_extract_wav_creates_file_and_caller_deletes():
    p = extract_wav(CLIP, 0.0, 3.0)
    try:
        assert os.path.exists(p) and p.endswith(".wav") and os.path.getsize(p) > 0
    finally:
        os.remove(p)


@pytest.mark.slow
def test_extract_sample_returns_bytes():
    b = extract_sample(CLIP, 0.0, 2.0)
    assert isinstance(b, bytes) and len(b) > 0
