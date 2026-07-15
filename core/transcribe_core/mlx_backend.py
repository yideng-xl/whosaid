"""MLX 推理后端：mlx-whisper 转写 + pyannote 说话人分离（仅 Apple Silicon）。"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile

from .backend import InferenceBackend, Turn, dedup_segments
from .transcript import Segment

DEFAULT_WHISPER = "mlx-community/whisper-large-v3-mlx"
DEFAULT_DIARIZE = "pyannote/speaker-diarization-community-1"

# mlx_whisper.load_model 期望的 ModelDimensions 字段
_MLX_DIM_KEYS = (
    "n_mels", "n_audio_ctx", "n_audio_state", "n_audio_head", "n_audio_layer",
    "n_vocab", "n_text_ctx", "n_text_state", "n_text_head", "n_text_layer",
)


def hf_config_to_mlx_dims(cfg: dict) -> dict:
    """把 HF transformers 版 whisper config 翻成 mlx_whisper 的 ModelDimensions 字段。

    部分 mlx-community 仓库（如 Belle 中文微调）权重已是 mlx 命名，但 config.json 仍是
    HF 格式（d_model/encoder_layers/num_mel_bins…）。mlx_whisper 直接 ModelDimensions(**config)
    会因多余键（activation_dropout 等）报错，故按语义一一翻译成它认的 10 个维度字段。
    """
    return {
        "n_mels": cfg["num_mel_bins"],
        "n_audio_ctx": cfg["max_source_positions"],
        "n_audio_state": cfg["d_model"],
        "n_audio_head": cfg["encoder_attention_heads"],
        "n_audio_layer": cfg["encoder_layers"],
        "n_vocab": cfg["vocab_size"],
        "n_text_ctx": cfg["max_target_positions"],
        "n_text_state": cfg["d_model"],
        "n_text_head": cfg["decoder_attention_heads"],
        "n_text_layer": cfg["decoder_layers"],
    }


def resolve_mlx_model(repo: str) -> str:
    """返回一个 mlx_whisper 可直接加载的模型目录路径。

    标准 mlx 布局（config 有 n_mels 且有 weights.safetensors/npz）直接返回缓存目录。
    对 config/权重文件名仍是 HF 式、但张量键名已是 mlx 的仓库（如 Belle 中文微调），
    生成一个规范化小目录：翻好的 config.json + 把 model.safetensors 符号链接成
    weights.safetensors（不复制几个 GB 权重）。已下载才会调用，故用 local_files_only。
    """
    from huggingface_hub import snapshot_download

    src = snapshot_download(repo, local_files_only=True)
    with open(os.path.join(src, "config.json"), "r", encoding="utf-8") as f:
        cfg = json.load(f)
    has_mlx_weights = os.path.exists(os.path.join(src, "weights.safetensors")) or \
        os.path.exists(os.path.join(src, "weights.npz"))
    is_mlx_config = "n_mels" in cfg
    if is_mlx_config and has_mlx_weights:
        return src  # 标准 mlx 仓库，无需处理

    dims = {k: cfg[k] for k in _MLX_DIM_KEYS} if is_mlx_config else hf_config_to_mlx_dims(cfg)
    dst = os.path.join(os.path.expanduser("~/.cache/whosaid/mlx-fix"),
                       repo.replace("/", "__"))
    os.makedirs(dst, exist_ok=True)
    # 权重：链接到已有的 weights.* 或 HF 命名的 model.safetensors，统一成 mlx 认的名字
    for cand, target in (("weights.safetensors", "weights.safetensors"),
                         ("weights.npz", "weights.npz"),
                         ("model.safetensors", "weights.safetensors")):
        p = os.path.join(src, cand)
        if os.path.exists(p):
            link = os.path.join(dst, target)
            if not os.path.lexists(link):
                os.symlink(os.path.realpath(p), link)
            break
    with open(os.path.join(dst, "config.json"), "w", encoding="utf-8") as f:
        json.dump({**dims, "model_type": "whisper"}, f)
    return dst


class MlxBackend(InferenceBackend):
    id = "mlx"

    def __init__(self, whisper_repo: str = DEFAULT_WHISPER, diarize_repo: str = DEFAULT_DIARIZE):
        self.whisper_repo = whisper_repo
        self.diarize_repo = diarize_repo

    def transcribe(self, audio_path, language, initial_prompt):
        import mlx_whisper

        kwargs = {
            # 规范化模型目录：兼容 config/权重文件名仍是 HF 式的仓库（如 Belle 中文微调）
            "path_or_hf_repo": resolve_mlx_model(self.whisper_repo),
            "condition_on_previous_text": False,  # 抑制重复幻觉传染
        }
        if language:
            kwargs["language"] = language
        if initial_prompt:
            kwargs["initial_prompt"] = initial_prompt
        result = mlx_whisper.transcribe(audio_path, **kwargs)
        segs = [
            Segment(seg["start"], seg["end"], seg["text"].strip())
            for seg in result.get("segments", [])
            if seg.get("text", "").strip()
        ]
        return dedup_segments(segs)

    def diarize(self, audio_path, num_speakers) -> list[Turn]:
        from pyannote.audio import Pipeline
        import torch
        import torchaudio

        token = os.environ.get("HF_TOKEN")  # None → 用 huggingface-cli 缓存 token
        pipeline = Pipeline.from_pretrained(self.diarize_repo, token=token)
        if torch.backends.mps.is_available():
            pipeline.to(torch.device("mps"))

        # 先 ffmpeg 转 16k 单声道 wav 整体读入，规避 pyannote 分块解码 m4a 的样本数 bug
        # 使用 mkstemp 替代已弃用的 mktemp，确保即使异常也会清理临时文件
        fd, tmp_wav = tempfile.mkstemp(suffix=".wav")
        os.close(fd)  # 关闭文件描述符，让 ffmpeg 可写
        try:
            subprocess.run(
                ["ffmpeg", "-i", audio_path, "-ar", "16000", "-ac", "1", tmp_wav, "-y", "-loglevel", "error"],
                check=True,
            )
            waveform, sample_rate = torchaudio.load(tmp_wav)
        finally:
            # 确保任何路径都清理临时文件（即使 ffmpeg 或 torchaudio.load 抛异常）
            os.remove(tmp_wav)

        kw = {"num_speakers": num_speakers} if num_speakers else {}
        output = pipeline({"waveform": waveform, "sample_rate": sample_rate}, **kw)
        diar = getattr(output, "exclusive_speaker_diarization", None) or getattr(
            output, "speaker_diarization", output
        )
        return [(t.start, t.end, spk) for t, _, spk in diar.itertracks(yield_label=True)]
