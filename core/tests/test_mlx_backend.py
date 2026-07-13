"""MlxBackend 集成测试：依赖真实 mlx-whisper / pyannote 模型，标记为 slow。"""
import os
import subprocess
import pytest

from transcribe_core.mlx_backend import MlxBackend
from transcribe_core.transcript import Segment


@pytest.fixture
def tiny_wav(tmp_path):
    """用 ffmpeg 生成 3 秒正弦波 wav，仅验证管道结构不校验内容。"""
    p = tmp_path / "tone.wav"
    subprocess.run(
        ["ffmpeg", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-ar", "16000", "-ac", "1", str(p), "-y", "-loglevel", "error"],
        check=True,
    )
    return str(p)


@pytest.mark.slow
def test_transcribe_returns_segments(tiny_wav):
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    be = MlxBackend()
    segs = be.transcribe(tiny_wav, language="zh", initial_prompt=None)
    assert isinstance(segs, list)
    assert all(isinstance(s, Segment) and s.speaker is None for s in segs)


@pytest.mark.slow
def test_diarize_returns_turns(tiny_wav):
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    be = MlxBackend()
    turns = be.diarize(tiny_wav, num_speakers=None)
    assert isinstance(turns, list)
    assert all(len(t) == 3 for t in turns)
