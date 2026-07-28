from types import SimpleNamespace

from transcribe_core.faster_whisper_backend import FasterWhisperBackend


class FakeModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio_path, **kwargs):
        self.calls.append((audio_path, kwargs))
        return iter([
            SimpleNamespace(start=0.0, end=1.5, text=" 你好 "),
            SimpleNamespace(start=1.5, end=2.0, text=""),
            SimpleNamespace(start=2.0, end=3.0, text="世界"),
            SimpleNamespace(start=3.0, end=4.0, text="世界"),
        ]), SimpleNamespace(language="zh")


def test_transcribe_uses_cpu_int8_and_normalizes_segments():
    model = FakeModel()
    created = []

    def factory(repo, **kwargs):
        created.append((repo, kwargs))
        return model

    backend = FasterWhisperBackend(
        "Systran/faster-whisper-small",
        "pyannote/repo",
        model_factory=factory,
        resolve_model=lambda repo: f"C:/cache/{repo}",
    )

    result = backend.transcribe("C:/audio.wav", "zh", "会议")

    assert created == [(
        "C:/cache/Systran/faster-whisper-small",
        {"device": "cpu", "compute_type": "int8"},
    )]
    assert model.calls == [(
        "C:/audio.wav",
        {
            "condition_on_previous_text": False,
            "language": "zh",
            "initial_prompt": "会议",
        },
    )]
    assert [(s.start, s.end, s.text) for s in result] == [
        (0.0, 1.5, "你好"),
        (2.0, 3.0, "世界"),
    ]


def test_model_is_reused_for_multiple_blocks():
    model = FakeModel()
    calls = []
    backend = FasterWhisperBackend(
        "repo",
        "diarize",
        model_factory=lambda repo, **kwargs: calls.append(repo) or model,
        resolve_model=lambda repo: repo,
    )

    backend.transcribe("a.wav", None, None)
    backend.transcribe("b.wav", None, None)

    assert calls == ["repo"]


def test_diarize_reuses_shared_engine(monkeypatch):
    seen = []

    class FakeEngine:
        def diarize(self, audio_path, num_speakers):
            seen.append((audio_path, num_speakers))
            return [(0.0, 2.0, "SPEAKER_00")]

    monkeypatch.setattr(
        "transcribe_core.diarize.make_engine",
        lambda repo: FakeEngine(),
    )
    backend = FasterWhisperBackend("repo", "diarize")

    assert backend.diarize("meeting.wav", 2) == [(0.0, 2.0, "SPEAKER_00")]
    assert seen == [("meeting.wav", 2)]
