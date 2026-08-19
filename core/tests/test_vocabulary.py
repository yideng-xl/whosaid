import json

import pytest

from transcribe_core.vocabulary import VocabularyStore


def make_store(tmp_path):
    ids = iter(["entry-1", "entry-2", "entry-3"])
    times = iter([10.0, 20.0, 30.0, 40.0])
    return VocabularyStore(
        tmp_path / "vocabulary.json",
        id_factory=lambda: next(ids),
        clock=lambda: next(times),
    )


def test_crud_persists_and_normalizes_aliases(tmp_path):
    store = make_store(tmp_path)
    person = store.add("person", " 许磊 ", ["许雷", "许雷", "许磊"])
    assert person["canonical"] == "许磊"
    assert person["aliases"] == ["许雷"]

    updated = store.update(
        person["id"], kind="person", canonical="许磊",
        aliases=["徐磊"], enabled=False,
    )
    assert updated["enabled"] is False
    assert updated["created_at"] == 10.0
    assert updated["updated_at"] == 20.0

    reloaded = VocabularyStore(tmp_path / "vocabulary.json")
    assert reloaded.list() == [updated]
    reloaded.delete(person["id"])
    assert reloaded.list() == []


def test_duplicate_standard_form_is_rejected_within_same_kind(tmp_path):
    store = make_store(tmp_path)
    store.add("term", "端到端探测")
    with pytest.raises(ValueError, match="已存在"):
        store.add("term", " 端到端探测 ")
    # 同一写法可分别作为姓名和术语保存，避免替用户推断语义。
    store.add("person", "端到端探测")


def test_invalid_file_is_not_silently_overwritten(tmp_path):
    path = tmp_path / "vocabulary.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="无法读取"):
        VocabularyStore(path)
    assert path.read_text(encoding="utf-8") == "{broken"


def test_build_prompt_uses_only_enabled_canonical_forms(tmp_path):
    store = make_store(tmp_path)
    store.add("person", "许磊", ["许雷"])
    store.add("term", "端到端探测", ["端到端弹策"])
    store.add("term", "不启用", enabled=False)

    prompt = store.build_prompt()

    assert prompt == (
        "以下是本次会议可能出现的标准写法。"
        "姓名：许磊。专用词：端到端探测。请优先使用这些标准写法。"
    )
    assert "许雷" not in prompt
    assert "不启用" not in prompt


def test_build_prompt_obeys_complete_term_boundary(tmp_path):
    store = make_store(tmp_path)
    store.add("term", "甲")
    store.add("term", "这是很长的第二个词")
    prompt = store.build_prompt(max_chars=43)
    assert prompt == "以下是本次会议可能出现的标准写法。专用词：甲。请优先使用这些标准写法。"


def test_saved_document_has_explicit_version(tmp_path):
    store = make_store(tmp_path)
    store.add("person", "张三")
    payload = json.loads((tmp_path / "vocabulary.json").read_text(encoding="utf-8"))
    assert payload["version"] == 1


def test_failed_write_does_not_change_in_memory_entries(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    store.add("person", "张三")
    before = store.list()
    monkeypatch.setattr(store, "_save_locked", lambda entries: (_ for _ in ()).throw(
        OSError("disk full")
    ))

    with pytest.raises(OSError, match="disk full"):
        store.add("term", "端到端探测")
    assert store.list() == before
