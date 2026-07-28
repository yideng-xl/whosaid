"""diarize 子包工厂测试：不加载真实模型，仅验证按 repo 选引擎的路由逻辑。"""
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import wave

import pytest

from transcribe_core.diarize import make_engine
from transcribe_core.diarize.base import DiarizeEngine


def test_make_engine_returns_pyannote_for_pyannote_repo():
    eng = make_engine("pyannote/speaker-diarization-community-1")
    assert isinstance(eng, DiarizeEngine)
    assert eng.__class__.__name__ == "PyannoteEngine"


def test_make_engine_unknown_repo_raises():
    with pytest.raises(ValueError):
        make_engine("unknown/whatever")


def test_load_pcm16_wav_without_torchaudio(tmp_path):
    """Windows 包不依赖 TorchCodec 解码 ffmpeg 已转好的 PCM WAV。"""
    wav_path = tmp_path / "tone.wav"
    sample_rate = 16_000
    samples = [
        int(10_000 * math.sin(2 * math.pi * 440 * i / sample_rate))
        for i in range(160)
    ]
    with wave.open(str(wav_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(struct.pack(f"<{len(samples)}h", *samples))

    script = """
import json
import sys
from transcribe_core.diarize.pyannote_engine import _load_pcm16_wav
waveform, sample_rate = _load_pcm16_wav(sys.argv[1])
print(json.dumps({
    "sample_rate": sample_rate,
    "shape": list(waveform.shape),
    "floating": waveform.dtype.is_floating_point,
    "maximum": waveform.abs().max().item(),
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(wav_path)],
        check=True,
        capture_output=True,
        text=True,
        cwd=Path(__file__).parents[1],
    )
    loaded = json.loads(result.stdout)

    assert loaded["sample_rate"] == sample_rate
    assert loaded["shape"] == [1, len(samples)]
    assert loaded["floating"] is True
    assert loaded["maximum"] == pytest.approx(10_000 / 32_768, rel=1e-4)
