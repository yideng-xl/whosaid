"""diarize 子包工厂测试：不加载真实模型，仅验证按 repo 选引擎的路由逻辑。"""
from transcribe_core.diarize import make_engine
from transcribe_core.diarize.base import DiarizeEngine


def test_make_engine_returns_pyannote_for_pyannote_repo():
    eng = make_engine("pyannote/speaker-diarization-community-1")
    assert isinstance(eng, DiarizeEngine)
    assert eng.__class__.__name__ == "PyannoteEngine"


def test_make_engine_unknown_repo_raises():
    import pytest
    with pytest.raises(ValueError):
        make_engine("unknown/whatever")
