# tests/test_models.py
from transcribe_core.models import ModelRegistry, AVAILABLE


def test_defaults_and_active(tmp_path):
    reg = ModelRegistry(str(tmp_path / "config.json"),
                        is_downloaded_fn=lambda repo: False,
                        download_fn=lambda repo: None)
    assert reg.active("transcribe") == "whisper-large-v3"
    assert reg.active("diarize") == "pyannote-community-1"


def test_download_marks_downloaded_and_persists(tmp_path):
    cfg = str(tmp_path / "config.json")
    calls = []
    reg = ModelRegistry(cfg, is_downloaded_fn=lambda repo: False,
                        download_fn=lambda repo: calls.append(repo))
    reg.download("whisper-small")
    assert len(calls) == 1
    # 新实例从磁盘恢复，应记得已下载
    reg2 = ModelRegistry(cfg, is_downloaded_fn=lambda repo: False,
                         download_fn=lambda repo: None)
    ids = {m["id"]: m for m in reg2.list_models()}
    assert ids["whisper-small"]["downloaded"] is True


def test_set_active_switches_within_kind(tmp_path):
    reg = ModelRegistry(str(tmp_path / "config.json"),
                        is_downloaded_fn=lambda repo: True,
                        download_fn=lambda repo: None)
    reg.set_active("whisper-small")
    assert reg.active("transcribe") == "whisper-small"
    assert reg.active("diarize") == "pyannote-community-1"  # 未受影响


def test_list_marks_active(tmp_path):
    reg = ModelRegistry(str(tmp_path / "config.json"),
                        is_downloaded_fn=lambda repo: True,
                        download_fn=lambda repo: None)
    active_ids = {m["id"] for m in reg.list_models() if m["active"]}
    assert active_ids == {"whisper-large-v3", "pyannote-community-1"}


def test_available_has_five_whisper_and_one_pyannote():
    kinds = [m.kind for m in AVAILABLE]
    assert kinds.count("transcribe") == 5
    assert kinds.count("diarize") == 1
