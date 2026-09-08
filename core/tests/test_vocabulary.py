import json

import pytest

from transcribe_core.vocabulary import VocabularyStore


def make_store(tmp_path):
    ids = iter([f"library-{index}" for index in range(1, 20)])
    times = iter(float(index * 10) for index in range(1, 30))
    return VocabularyStore(
        tmp_path / "vocabulary.json",
        id_factory=lambda: next(ids),
        clock=lambda: next(times),
    )


def test_library_crud_persists_and_normalizes_terms(tmp_path):
    store = make_store(tmp_path)
    names = store.add(" 姓名 ", "general", ["赵甲", "赵甲", " 张三 "])
    assert names["name"] == "姓名"
    assert names["terms"] == ["赵甲", "张三"]

    updated = store.update(
        names["id"], name="常用姓名", scope="general", terms=["赵甲", "李四"]
    )
    assert updated["created_at"] == 10.0
    assert updated["updated_at"] == 20.0
    assert VocabularyStore(tmp_path / "vocabulary.json").list() == [updated]

    reloaded = VocabularyStore(tmp_path / "vocabulary.json")
    reloaded.delete(names["id"])
    assert reloaded.list() == []


def test_duplicate_library_name_is_rejected_case_insensitively(tmp_path):
    store = make_store(tmp_path)
    store.add("WhoSaid", "general", [])
    with pytest.raises(ValueError, match="同名"):
        store.add("whosaid", "specialized", ["示例探测"])


def test_default_prompt_uses_only_general_libraries(tmp_path):
    store = make_store(tmp_path)
    general = store.add("姓名", "general", ["赵甲", "张三"])
    store.add("产品甲", "specialized", ["示例词190", "示例追溯"])

    prompt, selected = store.build_prompt_snapshot(None)

    assert selected == [general["id"]]
    assert prompt == (
        "以下是本次会议可能出现的标准写法。"
        "词库“姓名”：赵甲、张三。请优先使用这些标准写法。"
    )
    assert "示例词190" not in prompt


def test_explicit_selection_can_include_specialized_or_nothing(tmp_path):
    store = make_store(tmp_path)
    store.add("姓名", "general", ["赵甲"])
    product = store.add("产品甲", "specialized", ["示例词190", "示例追溯"])

    prompt, selected = store.build_prompt_snapshot([product["id"]])
    assert selected == [product["id"]]
    assert "词库“产品甲”：示例词190、示例追溯。" in prompt
    assert "赵甲" not in prompt
    assert store.build_prompt_snapshot([]) == (None, [])


def test_unknown_selected_library_is_rejected(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(ValueError, match="不存在"):
        store.build_prompt_snapshot(["missing"])


def test_v1_file_migrates_to_named_libraries_without_disabled_terms(tmp_path):
    path = tmp_path / "vocabulary.json"
    path.write_text(json.dumps({
        "version": 1,
        "entries": [
            {"id": "p1", "kind": "person", "canonical": "赵甲", "aliases": [], "enabled": True},
            {"id": "t1", "kind": "term", "canonical": "示例探测", "aliases": [], "enabled": True},
            {"id": "o1", "kind": "other", "canonical": "停用内容", "aliases": [], "enabled": False},
        ],
    }), encoding="utf-8")

    libraries = make_store(tmp_path).list()

    assert [(item["name"], item["scope"], item["terms"]) for item in libraries] == [
        ("姓名", "general", ["赵甲"]),
        ("专用词库", "specialized", ["示例探测"]),
    ]
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 2


def test_invalid_file_is_not_silently_overwritten(tmp_path):
    path = tmp_path / "vocabulary.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="无法读取"):
        VocabularyStore(path)
    assert path.read_text(encoding="utf-8") == "{broken"


def test_write_failure_keeps_in_memory_libraries(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    store.add("姓名", "general", ["张三"])
    before = store.list()
    monkeypatch.setattr(store, "_save_locked", lambda rows: (_ for _ in ()).throw(
        OSError("disk full")
    ))
    with pytest.raises(OSError, match="disk full"):
        store.add("产品甲", "specialized", ["示例词190"])
    assert store.list() == before


def test_prompt_obeys_complete_term_boundary(tmp_path):
    store = make_store(tmp_path)
    library = store.add("短词", "general", ["甲", "这是很长的第二个词"])
    prompt, selected = store.build_prompt_snapshot([library["id"]], max_chars=45)
    assert selected == [library["id"]]
    assert "甲" in prompt
    assert "这是很长的第二个词" not in prompt
