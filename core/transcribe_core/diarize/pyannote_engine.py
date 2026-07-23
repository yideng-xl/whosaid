"""pyannote community-1 分离引擎（gated，需用户自带 HF token；权重不随包分发）。"""
from __future__ import annotations

import os
import subprocess
import tempfile

from .base import DiarizeEngine, Turn


class PyannoteEngine(DiarizeEngine):
    def __init__(self, repo: str):
        self.repo = repo

    def diarize(self, audio_path: str, num_speakers: int | None) -> list[Turn]:
        # 重依赖延迟到方法内导入：make_engine/构造实例不应触发模型加载，
        # 保证测试环境无预热也能跑（见 tests/test_diarize.py）。
        from pyannote.audio import Pipeline
        import torch
        import torchaudio

        token = os.environ.get("HF_TOKEN")  # None → 用 huggingface-cli 缓存 token
        pipeline = Pipeline.from_pretrained(self.repo, token=token)
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
