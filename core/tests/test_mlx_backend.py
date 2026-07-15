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


def test_hf_config_to_mlx_dims_translates_belle_config():
    """终审外发现的加载 bug 回归：HF 格式 whisper config 应翻成 mlx 的 10 个维度字段。
    数值取自 Belle-whisper-large-v3-zh 的真实 config。"""
    from transcribe_core.mlx_backend import hf_config_to_mlx_dims
    hf = {
        "num_mel_bins": 128, "max_source_positions": 1500, "d_model": 1280,
        "encoder_attention_heads": 20, "encoder_layers": 32, "vocab_size": 51866,
        "max_target_positions": 448, "decoder_attention_heads": 20, "decoder_layers": 32,
        "activation_dropout": 0.0, "model_type": "whisper",  # 多余键不应进入结果
    }
    dims = hf_config_to_mlx_dims(hf)
    assert dims == {
        "n_mels": 128, "n_audio_ctx": 1500, "n_audio_state": 1280, "n_audio_head": 20,
        "n_audio_layer": 32, "n_vocab": 51866, "n_text_ctx": 448, "n_text_state": 1280,
        "n_text_head": 20, "n_text_layer": 32,
    }
    # 结果只含 ModelDimensions 认的字段，绝无 activation_dropout 之类
    from mlx_whisper import whisper
    import dataclasses
    assert set(dims) == {f.name for f in dataclasses.fields(whisper.ModelDimensions)}
