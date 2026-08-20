"""可按会议选择的本地命名词库，以及面向转写后端的提示词快照。"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

SCOPES = {"general", "specialized"}
MAX_LIBRARIES = 100
MAX_TERMS = 1000
MAX_NAME_LENGTH = 80
MAX_TERM_LENGTH = 80


@dataclass(frozen=True)
class VocabularyLibrary:
    id: str
    name: str
    scope: str
    terms: list[str]
    created_at: float
    updated_at: float


class VocabularyStore:
    """以原子 JSON 文件保存命名词库；读写均在单进程锁内完成。"""

    def __init__(self, path: str | Path, *, id_factory: Callable[[], str] | None = None,
                 clock: Callable[[], float] | None = None) -> None:
        self.path = Path(path)
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._libraries, migrated = self._load()
        if migrated:
            self._save_locked(self._libraries)

    @staticmethod
    def _normalize_text(value: str, field: str, max_length: int) -> str:
        text = value.strip()
        if not text:
            raise ValueError(f"{field}不能为空")
        if len(text) > max_length:
            raise ValueError(f"{field}最长 {max_length} 个字符")
        return text

    @classmethod
    def _normalize_terms(cls, terms: list[str]) -> list[str]:
        if not isinstance(terms, list) or not all(isinstance(term, str) for term in terms):
            raise ValueError("词库内容必须为字符串列表")
        result: list[str] = []
        seen: set[str] = set()
        for value in terms:
            term = cls._normalize_text(value, "词条", MAX_TERM_LENGTH)
            key = term.casefold()
            if key not in seen:
                seen.add(key)
                result.append(term)
        return result

    @classmethod
    def _validated_library(cls, raw: dict) -> VocabularyLibrary:
        library_id = raw.get("id")
        if not isinstance(library_id, str) or not library_id.strip():
            raise ValueError("词库缺少 id")
        scope = raw.get("scope")
        if scope not in SCOPES:
            raise ValueError("词库类型必须为 general 或 specialized")
        name = cls._normalize_text(str(raw.get("name", "")), "词库名称", MAX_NAME_LENGTH)
        created_at = float(raw.get("created_at", 0))
        updated_at = float(raw.get("updated_at", created_at))
        return VocabularyLibrary(
            id=library_id.strip(), name=name, scope=scope,
            terms=cls._normalize_terms(raw.get("terms", [])),
            created_at=created_at, updated_at=updated_at,
        )

    def _migrate_v1(self, rows: list[dict]) -> list[VocabularyLibrary]:
        """把旧版三个固定类别迁成三个命名词库；停用词条不再参与提示词。"""
        mapping = {
            "person": ("姓名", "general"),
            "term": ("专用词库", "specialized"),
            "other": ("其他", "specialized"),
        }
        grouped: dict[str, list[str]] = {kind: [] for kind in mapping}
        for row in rows:
            if not isinstance(row, dict) or row.get("kind") not in mapping:
                raise ValueError("旧版词库条目格式无效")
            if row.get("enabled", True) is False:
                continue
            grouped[row["kind"]].append(self._normalize_text(
                str(row.get("canonical", "")), "标准写法", MAX_TERM_LENGTH
            ))
        migrated: list[VocabularyLibrary] = []
        for kind in ("person", "term", "other"):
            terms = self._normalize_terms(grouped[kind])
            if not terms:
                continue
            now = self._clock()
            name, scope = mapping[kind]
            migrated.append(VocabularyLibrary(
                id=self._id_factory(), name=name, scope=scope, terms=terms,
                created_at=now, updated_at=now,
            ))
        return migrated

    def _load(self) -> tuple[list[VocabularyLibrary], bool]:
        if not self.path.exists():
            return [], False
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"词库文件无法读取：{self.path}") from error
        if not isinstance(payload, dict):
            raise ValueError("不支持的词库文件版本")
        version = payload.get("version")
        if version == 1:
            rows = payload.get("entries")
            if not isinstance(rows, list) or len(rows) > MAX_TERMS:
                raise ValueError("旧版词库条目格式或数量无效")
            return self._migrate_v1(rows), True
        if version != 2:
            raise ValueError("不支持的词库文件版本")
        rows = payload.get("libraries")
        if not isinstance(rows, list) or len(rows) > MAX_LIBRARIES:
            raise ValueError("词库格式或数量无效")
        libraries = [self._validated_library(row) for row in rows if isinstance(row, dict)]
        if len(libraries) != len(rows) or len({item.id for item in libraries}) != len(libraries):
            raise ValueError("词库格式无效或 id 重复")
        if sum(len(item.terms) for item in libraries) > MAX_TERMS:
            raise ValueError(f"所有词库合计最多保存 {MAX_TERMS} 个词条")
        return libraries, False

    def _save_locked(self, libraries: list[VocabularyLibrary]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{threading.get_ident()}.tmp")
        encoded = json.dumps(
            {"version": 2, "libraries": [asdict(item) for item in libraries]},
            ensure_ascii=False, indent=2,
        ).encode("utf-8")
        try:
            with temporary.open("wb") as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def _ensure_unique_name(self, name: str, exclude_id: str | None = None) -> None:
        key = name.casefold()
        if any(item.id != exclude_id and item.name.casefold() == key for item in self._libraries):
            raise ValueError("已存在同名词库")

    @staticmethod
    def _validate_total(libraries: list[VocabularyLibrary]) -> None:
        if len(libraries) > MAX_LIBRARIES:
            raise ValueError(f"最多保存 {MAX_LIBRARIES} 个词库")
        if sum(len(item.terms) for item in libraries) > MAX_TERMS:
            raise ValueError(f"所有词库合计最多保存 {MAX_TERMS} 个词条")

    def list(self) -> list[dict]:
        with self._lock:
            return [asdict(item) for item in self._libraries]

    def add(self, name: str, scope: str, terms: list[str]) -> dict:
        if scope not in SCOPES:
            raise ValueError("词库类型必须为 general 或 specialized")
        normalized_name = self._normalize_text(name, "词库名称", MAX_NAME_LENGTH)
        normalized_terms = self._normalize_terms(terms)
        with self._lock:
            self._ensure_unique_name(normalized_name)
            now = self._clock()
            library = VocabularyLibrary(
                id=self._id_factory(), name=normalized_name, scope=scope,
                terms=normalized_terms, created_at=now, updated_at=now,
            )
            next_libraries = [*self._libraries, library]
            self._validate_total(next_libraries)
            self._save_locked(next_libraries)
            self._libraries = next_libraries
            return asdict(library)

    def update(self, library_id: str, *, name: str, scope: str, terms: list[str]) -> dict:
        if scope not in SCOPES:
            raise ValueError("词库类型必须为 general 或 specialized")
        normalized_name = self._normalize_text(name, "词库名称", MAX_NAME_LENGTH)
        normalized_terms = self._normalize_terms(terms)
        with self._lock:
            index = next((i for i, item in enumerate(self._libraries) if item.id == library_id), None)
            if index is None:
                raise KeyError(library_id)
            self._ensure_unique_name(normalized_name, exclude_id=library_id)
            old = self._libraries[index]
            library = VocabularyLibrary(
                id=old.id, name=normalized_name, scope=scope, terms=normalized_terms,
                created_at=old.created_at, updated_at=self._clock(),
            )
            next_libraries = list(self._libraries)
            next_libraries[index] = library
            self._validate_total(next_libraries)
            self._save_locked(next_libraries)
            self._libraries = next_libraries
            return asdict(library)

    def delete(self, library_id: str) -> None:
        with self._lock:
            index = next((i for i, item in enumerate(self._libraries) if item.id == library_id), None)
            if index is None:
                raise KeyError(library_id)
            next_libraries = list(self._libraries)
            del next_libraries[index]
            self._save_locked(next_libraries)
            self._libraries = next_libraries

    def build_prompt_snapshot(self, library_ids: list[str] | None,
                              max_chars: int = 5000,
                              max_entries: int = 300) -> tuple[str | None, list[str]]:
        """返回提示词和实际选择。None 默认选通用词库，空列表表示明确不使用。"""
        if library_ids is not None and (
            not isinstance(library_ids, list)
            or not all(isinstance(item, str) and item.strip() for item in library_ids)
        ):
            raise ValueError("词库选择必须为非空 id 列表")
        with self._lock:
            libraries = list(self._libraries)
        if library_ids is None:
            selected = [item for item in libraries if item.scope == "general"]
        else:
            requested = list(dict.fromkeys(item.strip() for item in library_ids))
            known = {item.id for item in libraries}
            missing = [item for item in requested if item not in known]
            if missing:
                raise ValueError(f"选择了不存在的词库：{missing[0]}")
            selected_set = set(requested)
            selected = [item for item in libraries if item.id in selected_set]
        selected_ids = [item.id for item in selected]
        if max_chars <= 0 or max_entries <= 0 or not selected:
            return None, selected_ids

        prefix = "以下是本次会议可能出现的标准写法。"
        suffix = "请优先使用这些标准写法。"
        sections: list[str] = []
        count = 0
        for library in selected:
            included: list[str] = []
            for term in library.terms:
                if count >= max_entries:
                    break
                candidate = f"词库“{library.name}”：{'、'.join([*included, term])}。"
                if len(prefix + "".join([*sections, candidate]) + suffix) > max_chars:
                    continue
                included.append(term)
                count += 1
            if included:
                sections.append(f"词库“{library.name}”：{'、'.join(included)}。")
        return (prefix + "".join(sections) + suffix if sections else None), selected_ids

    def build_prompt(self, max_chars: int = 5000, max_entries: int = 300) -> str | None:
        """兼容旧调用：默认只使用全部通用词库。"""
        return self.build_prompt_snapshot(None, max_chars, max_entries)[0]
