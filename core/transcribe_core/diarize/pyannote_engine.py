"""pyannote community-1 分离引擎（gated，需用户自带 HF token；权重不随包分发）。"""
from __future__ import annotations

import os
import subprocess
import tempfile
import wave
from os import PathLike

from .base import DiarizeEngine, Turn


def _load_pcm16_wav(path: str | PathLike[str]):
    """读取 ffmpeg 产出的 PCM16 WAV，绕开 TorchCodec/系统 FFmpeg 动态库。"""
    import torch

    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        compression = wav.getcomptype()
        frames = wav.readframes(wav.getnframes())

    if sample_width != 2 or compression != "NONE":
        raise ValueError(
            f"只支持未压缩 PCM16 WAV，实际 sample_width={sample_width}, compression={compression}"
        )

    samples = torch.frombuffer(bytearray(frames), dtype=torch.int16)
    waveform = samples.reshape(-1, channels).transpose(0, 1).to(torch.float32)
    return waveform / 32_768.0, sample_rate


class PyannoteEngine(DiarizeEngine):
    def __init__(self, repo: str):
        self.repo = repo

    def diarize(self, audio_path: str, num_speakers: int | None) -> list[Turn]:
        # 重依赖延迟到方法内导入：make_engine/构造实例不应触发模型加载，
        # 保证测试环境无预热也能跑（见 tests/test_diarize.py）。
        from pyannote.audio import Pipeline
        import torch

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
                [
                    "ffmpeg",
                    "-i",
                    audio_path,
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-c:a",
                    "pcm_s16le",
                    tmp_wav,
                    "-y",
                    "-loglevel",
                    "error",
                ],
                check=True,
            )
            waveform, sample_rate = _load_pcm16_wav(tmp_wav)
        finally:
            # 确保任何路径都清理临时文件（即使 ffmpeg 或 WAV 读取抛异常）
            os.remove(tmp_wav)

        kw = {"num_speakers": num_speakers} if num_speakers else {}
        output = pipeline({"waveform": waveform, "sample_rate": sample_rate}, **kw)
        diar = getattr(output, "exclusive_speaker_diarization", None) or getattr(
            output, "speaker_diarization", output
        )
        return [(t.start, t.end, spk) for t, _, spk in diar.itertracks(yield_label=True)]
