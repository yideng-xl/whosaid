# tests/test_models.py
from transcribe_core.models import ModelRegistry, AVAILABLE, models_for_backend


def test_defaults_and_active(tmp_path):
    reg = ModelRegistry(str(tmp_path / "config.json"),
                        is_downloaded_fn=lambda repo: False,
                        download_fn=lambda repo: None)
    assert reg.active("transcribe") == "whisper-large-v3"
    assert reg.active("diarize") == "pyannote-community-1"


def test_download_marks_downloaded_and_persists(tmp_path):
    cfg = str(tmp_path / "config.json")
    cache = set()  # 假缓存：下载后真的落盘，重启后仍在
    reg = ModelRegistry(cfg, is_downloaded_fn=lambda repo: repo in cache,
                        download_fn=lambda repo: cache.add(repo))
    reg.download("whisper-small")
    assert len(cache) == 1
    # 新实例从磁盘恢复，应记得已下载
    reg2 = ModelRegistry(cfg, is_downloaded_fn=lambda repo: repo in cache,
                         download_fn=lambda repo: None)
    ids = {m["id"]: m for m in reg2.list_models()}
    assert ids["whisper-small"]["downloaded"] is True


def test_downloaded_list_does_not_mask_missing_cache(tmp_path):
    """config.json 记着「已下载」但本地缓存实际不在（用户手动清缓存、换了 HF_HOME 等），
    必须如实报 False——否则前端显示已就绪，转写时才炸在 local_files_only 上。"""
    cfg = tmp_path / "config.json"
    cfg.write_text('{"active": {}, "downloaded": ["whisper-small"]}', encoding="utf-8")
    reg = ModelRegistry(str(cfg), is_downloaded_fn=lambda repo: False,
                        download_fn=lambda repo: None)
    ids = {m["id"]: m for m in reg.list_models()}
    assert ids["whisper-small"]["downloaded"] is False


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


def test_available_has_transcribe_and_one_pyannote():
    kinds = [m.kind for m in AVAILABLE]
    # 5 个原版 whisper（tiny/base/small/medium/large-v3）+ 2 个 Belle 中文微调
    assert kinds.count("transcribe") == 7
    assert kinds.count("diarize") == 1
    ids = {m.id for m in AVAILABLE}
    assert {"belle-v3-zh-punct", "belle-v3-turbo-zh"} <= ids  # 中文微调已登记


def test_faster_whisper_catalog_uses_converted_models_and_hides_belle():
    models = models_for_backend("faster-whisper")
    ids = {m.id for m in models}
    repos = {m.id: m.repo for m in models}

    assert "whisper-small" in ids
    assert "belle-v3-zh-punct" not in ids
    assert "belle-v3-turbo-zh" not in ids
    assert repos["whisper-small"] == "Systran/faster-whisper-small"
    assert repos["whisper-large-v3"] == "Systran/faster-whisper-large-v3"
    assert "pyannote-community-1" in ids


def test_active_repo_returns_current_active_models_repo(tmp_path):
    reg = ModelRegistry(str(tmp_path / "config.json"),
                        is_downloaded_fn=lambda repo: True,
                        download_fn=lambda repo: None)
    assert reg.active_repo("transcribe") == "mlx-community/whisper-large-v3-mlx"
    reg.set_active("whisper-small")
    assert reg.active_repo("transcribe") == "mlx-community/whisper-small-mlx"


def test_get_settings_defaults_to_none(tmp_path):
    reg = ModelRegistry(str(tmp_path / "config.json"),
                        is_downloaded_fn=lambda repo: False,
                        download_fn=lambda repo: None)
    assert reg.get_settings() == {"hf_token": None, "hf_endpoint": None}


def test_set_settings_persists_across_instances(tmp_path):
    cfg = str(tmp_path / "config.json")
    reg = ModelRegistry(cfg, is_downloaded_fn=lambda repo: False, download_fn=lambda repo: None)
    reg.set_settings("hf_abc123", "https://hf-mirror.com")
    assert reg.get_settings() == {"hf_token": "hf_abc123", "hf_endpoint": "https://hf-mirror.com"}
    # 新实例从磁盘恢复，应记得设置
    reg2 = ModelRegistry(cfg, is_downloaded_fn=lambda repo: False, download_fn=lambda repo: None)
    assert reg2.get_settings() == {"hf_token": "hf_abc123", "hf_endpoint": "https://hf-mirror.com"}


def test_set_settings_empty_string_clears_to_none(tmp_path):
    cfg = str(tmp_path / "config.json")
    reg = ModelRegistry(cfg, is_downloaded_fn=lambda repo: False, download_fn=lambda repo: None)
    reg.set_settings("hf_abc123", "https://hf-mirror.com")
    reg.set_settings("", "")
    assert reg.get_settings() == {"hf_token": None, "hf_endpoint": None}


def test_delete_removes_from_downloaded_calls_delete_fn_and_persists(tmp_path):
    cfg = str(tmp_path / "config.json")
    calls = []
    reg = ModelRegistry(cfg, is_downloaded_fn=lambda repo: False,
                        download_fn=lambda repo: None,
                        delete_fn=lambda repo: calls.append(repo))
    reg.download("whisper-small")
    reg.delete("whisper-small")
    assert calls == ["mlx-community/whisper-small-mlx"]
    ids = {m["id"]: m for m in reg.list_models()}
    assert ids["whisper-small"]["downloaded"] is False
    # 新实例从磁盘恢复，应记得已删除（不在 downloaded 列表里）
    reg2 = ModelRegistry(cfg, is_downloaded_fn=lambda repo: False, download_fn=lambda repo: None)
    ids2 = {m["id"]: m for m in reg2.list_models()}
    assert ids2["whisper-small"]["downloaded"] is False


def test_delete_without_delete_fn_raises_runtime_error(tmp_path):
    """未注入 delete_fn（如旧调用方/测试未传该参数）时，delete 应显式报错而非静默跳过，
    避免调用方误以为已删除。"""
    import pytest
    reg = ModelRegistry(str(tmp_path / "config.json"),
                        is_downloaded_fn=lambda repo: False,
                        download_fn=lambda repo: None)
    with pytest.raises(RuntimeError):
        reg.delete("whisper-small")


def test_delete_unknown_model_id_raises_keyerror(tmp_path):
    reg = ModelRegistry(str(tmp_path / "config.json"),
                        is_downloaded_fn=lambda repo: False,
                        download_fn=lambda repo: None,
                        delete_fn=lambda repo: None)
    import pytest
    with pytest.raises(KeyError):
        reg.delete("no-such-model")
