"""Windows/通用 CPU 转写后端：faster-whisper + pyannote。"""
from __future__ import annotations

from collections.abc import Callable

from .backend import InferenceBackend, Turn, dedup_segments
from .transcript import Segment


def _default_resolve_model(repo: str) -> str:
    from huggingface_hub import snapshot_download

    return snapshot_download(repo, local_files_only=True)


def _default_model_factory(model_path: str, **kwargs):
    from faster_whisper import WhisperModel

    return WhisperModel(model_path, **kwargs)


class FasterWhisperBackend(InferenceBackend):
    """CPU 优先的跨平台 Whisper 后端。

    一个任务会按说话人块多次调用 transcribe，因此模型在实例内只加载一次。
    """

    id = "faster-whisper"

    def __init__(
        self,
        whisper_repo: str,
        diarize_repo: str,
        *,
        model_factory: Callable | None = None,
        resolve_model: Callable[[str], str] | None = None,
    ):
        self.whisper_repo = whisper_repo
        self.diarize_repo = diarize_repo
        self._model_factory = model_factory or _default_model_factory
        self._resolve_model = resolve_model or _default_resolve_model
        self._model = None

    def _get_model(self):
        if self._model is None:
            model_path = self._resolve_model(self.whisper_repo)
            self._model = self._model_factory(
                model_path,
                device="cpu",
                compute_type="int8",
            )
        return self._model

    def transcribe(self, audio_path, language, initial_prompt):
        kwargs = {"condition_on_previous_text": False}
        if language:
            kwargs["language"] = language
        if initial_prompt:
            kwargs["initial_prompt"] = initial_prompt

        segments, _info = self._get_model().transcribe(audio_path, **kwargs)
        result = [
            Segment(float(seg.start), float(seg.end), seg.text.strip())
            for seg in segments
            if seg.text.strip()
        ]
        return dedup_segments(result)

    def diarize(self, audio_path: str, num_speakers: int | None) -> list[Turn]:
        from .diarize import make_engine

        return make_engine(self.diarize_repo).diarize(audio_path, num_speakers)
