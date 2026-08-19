"""本地姓名库与专用词库，以及面向转写后端的提示词生成。"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable


KINDS = {"person", "term"}
MAX_ENTRIES = 1000
MAX_ALIASES = 20
MAX_TEXT_LENGTH = 80


@dataclass(frozen=True)
class VocabularyEntry:
    id: str
    kind: str
    canonical: str
    aliases: list[str]
    enabled: bool
    created_at: float
    updated_at: float


class VocabularyStore:
    """以原子 JSON 文件保存词库；读写均在单进程锁内完成。"""

    def __init__(
        self,
        path: str | Path,
        *,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.path = Path(path)
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._entries = self._load()

    @staticmethod
    def _normalize_text(value: str, field: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError(f"{field}不能为空")
        if len(text) > MAX_TEXT_LENGTH:
            raise ValueError(f"{field}最长 {MAX_TEXT_LENGTH} 个字符")
        return text

    @classmethod
    def _normalize_aliases(cls, aliases: list[str], canonical: str) -> list[str]:
        if len(aliases) > MAX_ALIASES:
            raise ValueError(f"别名最多 {MAX_ALIASES} 个")
        result: list[str] = []
        seen = {canonical.casefold()}
        for value in aliases:
            alias = cls._normalize_text(value, "别名")
            key = alias.casefold()
            if key not in seen:
                seen.add(key)
                result.append(alias)
        return result

    @classmethod
    def _validated_entry(cls, raw: dict) -> VocabularyEntry:
        kind = raw.get("kind")
        if kind not in KINDS:
            raise ValueError("词库类型必须为 person 或 term")
        canonical = cls._normalize_text(str(raw.get("canonical", "")), "标准写法")
        aliases_raw = raw.get("aliases", [])
        if not isinstance(aliases_raw, list) or not all(isinstance(x, str) for x in aliases_raw):
            raise ValueError("别名必须为字符串列表")
        entry_id = raw.get("id")
        if not isinstance(entry_id, str) or not entry_id:
            raise ValueError("词库条目缺少 id")
        created_at = float(raw.get("created_at", 0))
        updated_at = float(raw.get("updated_at", created_at))
        enabled = raw.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("启用状态必须为布尔值")
        return VocabularyEntry(
            id=entry_id,
            kind=kind,
            canonical=canonical,
            aliases=cls._normalize_aliases(aliases_raw, canonical),
            enabled=enabled,
            created_at=created_at,
            updated_at=updated_at,
        )

    def _load(self) -> list[VocabularyEntry]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"词库文件无法读取：{self.path}") from error
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ValueError("不支持的词库文件版本")
        rows = payload.get("entries")
        if not isinstance(rows, list) or len(rows) > MAX_ENTRIES:
            raise ValueError("词库条目格式或数量无效")
        entries = [self._validated_entry(row) for row in rows if isinstance(row, dict)]
        if len(entries) != len(rows) or len({entry.id for entry in entries}) != len(entries):
            raise ValueError("词库条目格式无效或 id 重复")
        return entries

    def _save_locked(self, entries: list[VocabularyEntry]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f".{self.path.name}.{threading.get_ident()}.tmp"
        )
        encoded = json.dumps(
            {"version": 1, "entries": [asdict(entry) for entry in entries]},
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        try:
            with temporary.open("wb") as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def _ensure_unique(self, kind: str, canonical: str, exclude_id: str | None = None) -> None:
        key = canonical.casefold()
        if any(
            entry.kind == kind
            and entry.id != exclude_id
            and entry.canonical.casefold() == key
            for entry in self._entries
        ):
            raise ValueError("同类词库中已存在相同的标准写法")

    def list(self) -> list[dict]:
        with self._lock:
            return [asdict(entry) for entry in self._entries]

    def add(
        self, kind: str, canonical: str, aliases: list[str] | None = None,
        enabled: bool = True,
    ) -> dict:
        if kind not in KINDS:
            raise ValueError("词库类型必须为 person 或 term")
        normalized = self._normalize_text(canonical, "标准写法")
        normalized_aliases = self._normalize_aliases(aliases or [], normalized)
        with self._lock:
            if len(self._entries) >= MAX_ENTRIES:
                raise ValueError(f"词库最多保存 {MAX_ENTRIES} 条")
            self._ensure_unique(kind, normalized)
            now = self._clock()
            entry = VocabularyEntry(
                id=self._id_factory(),
                kind=kind,
                canonical=normalized,
                aliases=normalized_aliases,
                enabled=enabled,
                created_at=now,
                updated_at=now,
            )
            next_entries = [*self._entries, entry]
            self._save_locked(next_entries)
            self._entries = next_entries
            return asdict(entry)

    def update(
        self, entry_id: str, *, kind: str, canonical: str,
        aliases: list[str] | None = None, enabled: bool = True,
    ) -> dict:
        if kind not in KINDS:
            raise ValueError("词库类型必须为 person 或 term")
        normalized = self._normalize_text(canonical, "标准写法")
        normalized_aliases = self._normalize_aliases(aliases or [], normalized)
        with self._lock:
            index = next((i for i, entry in enumerate(self._entries) if entry.id == entry_id), None)
            if index is None:
                raise KeyError(entry_id)
            self._ensure_unique(kind, normalized, exclude_id=entry_id)
            old = self._entries[index]
            entry = VocabularyEntry(
                id=old.id,
                kind=kind,
                canonical=normalized,
                aliases=normalized_aliases,
                enabled=enabled,
                created_at=old.created_at,
                updated_at=self._clock(),
            )
            next_entries = list(self._entries)
            next_entries[index] = entry
            self._save_locked(next_entries)
            self._entries = next_entries
            return asdict(entry)

    def delete(self, entry_id: str) -> None:
        with self._lock:
            index = next((i for i, entry in enumerate(self._entries) if entry.id == entry_id), None)
            if index is None:
                raise KeyError(entry_id)
            next_entries = list(self._entries)
            del next_entries[index]
            self._save_locked(next_entries)
            self._entries = next_entries

    def build_prompt(self, max_chars: int = 1000, max_entries: int = 100) -> str | None:
        """只把启用条目的标准写法送给模型；别名留给后续校对，不诱导模型输出误写。"""
        if max_chars <= 0 or max_entries <= 0:
            return None
        with self._lock:
            enabled = [entry for entry in self._entries if entry.enabled][:max_entries]
        if not enabled:
            return None

        people: list[str] = []
        terms: list[str] = []
        prefix = "以下是本次会议可能出现的标准写法。"
        suffix = "请优先使用这些标准写法。"

        def render() -> str:
            sections = []
            if people:
                sections.append(f"姓名：{'、'.join(people)}。")
            if terms:
                sections.append(f"专用词：{'、'.join(terms)}。")
            return prefix + "".join(sections) + suffix

        for entry in enabled:
            target = people if entry.kind == "person" else terms
            target.append(entry.canonical)
            if len(render()) > max_chars:
                target.pop()
                continue
        prompt = render()
        return prompt if people or terms else None
