"""按操作系统选择推理后端，并集中创建后端实例。"""
from __future__ import annotations

SUPPORTED_BACKENDS = {"mlx", "faster-whisper"}


def choose_backend_id(
    platform_name: str,
    machine: str,
    override: str | None,
) -> str:
    """返回推理后端 ID；显式覆盖优先于平台默认值。"""
    if override:
        if override not in SUPPORTED_BACKENDS:
            raise ValueError(f"不支持的推理后端：{override}")
        return override
    if platform_name == "darwin" and machine.lower() in {"arm64", "aarch64"}:
        return "mlx"
    return "faster-whisper"


def create_backend(backend_id: str, whisper_repo: str, diarize_repo: str):
    if backend_id == "mlx":
        from .mlx_backend import MlxBackend

        return MlxBackend(whisper_repo, diarize_repo)
    if backend_id == "faster-whisper":
        from .faster_whisper_backend import FasterWhisperBackend

        return FasterWhisperBackend(whisper_repo, diarize_repo)
    raise ValueError(f"不支持的推理后端：{backend_id}")
