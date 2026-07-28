import pytest

from transcribe_core.backend_selection import choose_backend_id, create_backend
from transcribe_core.faster_whisper_backend import FasterWhisperBackend
from transcribe_core.mlx_backend import MlxBackend


def test_apple_silicon_defaults_to_mlx():
    assert choose_backend_id("darwin", "arm64", None) == "mlx"


def test_windows_x64_defaults_to_faster_whisper():
    assert choose_backend_id("win32", "AMD64", None) == "faster-whisper"


def test_environment_override_wins():
    assert choose_backend_id("darwin", "arm64", "faster-whisper") == "faster-whisper"


def test_unknown_override_is_rejected():
    with pytest.raises(ValueError, match="不支持的推理后端"):
        choose_backend_id("win32", "AMD64", "unknown")


def test_create_backend_routes_to_platform_implementation():
    mlx = create_backend("mlx", "mlx/repo", "diarize/repo")
    faster = create_backend("faster-whisper", "ct2/repo", "diarize/repo")

    assert isinstance(mlx, MlxBackend)
    assert isinstance(faster, FasterWhisperBackend)
