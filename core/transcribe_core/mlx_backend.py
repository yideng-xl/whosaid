"""MLX 推理后端：mlx-whisper 转写 + pyannote 说话人分离（仅 Apple Silicon）。"""
from __future__ import annotations

import os
import subprocess
import tempfile

from .backend import InferenceBackend, Turn, dedup_segments
from .transcript import Segment

DEFAULT_WHISPER = "mlx-community/whisper-large-v3-mlx"
DEFAULT_DIARIZE = "pyannote/speaker-diarization-community-1"


class MlxBackend(InferenceBackend):
    id = "mlx"

    def __init__(self, whisper_repo: str = DEFAULT_WHISPER, diarize_repo: str = DEFAULT_DIARIZE):
        self.whisper_repo = whisper_repo
        self.diarize_repo = diarize_repo

    def transcribe(self, audio_path, language, initial_prompt):
        import mlx_whisper

        kwargs = {
            "path_or_hf_repo": self.whisper_repo,
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
        tmp_wav = tempfile.mktemp(suffix=".wav")
        subprocess.run(
            ["ffmpeg", "-i", audio_path, "-ar", "16000", "-ac", "1", tmp_wav, "-y", "-loglevel", "error"],
            check=True,
        )
        waveform, sample_rate = torchaudio.load(tmp_wav)
        os.remove(tmp_wav)

        kw = {"num_speakers": num_speakers} if num_speakers else {}
        output = pipeline({"waveform": waveform, "sample_rate": sample_rate}, **kw)
        diar = getattr(output, "exclusive_speaker_diarization", None) or getattr(
            output, "speaker_diarization", output
        )
        return [(t.start, t.end, spk) for t, _, spk in diar.itertracks(yield_label=True)]
