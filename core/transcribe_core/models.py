"""模型注册表：内置清单、下载状态、当前启用模型，状态持久化到 config.json。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class ModelInfo:
    id: str
    kind: str  # "transcribe" | "diarize"
    display_name: str
    repo: str
    size_mb: int


AVAILABLE: list[ModelInfo] = [
    ModelInfo("whisper-tiny", "transcribe", "Whisper Tiny（最快/最糙）", "mlx-community/whisper-tiny-mlx", 75),
    ModelInfo("whisper-base", "transcribe", "Whisper Base", "mlx-community/whisper-base-mlx", 145),
    ModelInfo("whisper-small", "transcribe", "Whisper Small", "mlx-community/whisper-small-mlx", 484),
    ModelInfo("whisper-medium", "transcribe", "Whisper Medium", "mlx-community/whisper-medium-mlx", 1530),
    ModelInfo("whisper-large-v3", "transcribe", "Whisper Large v3（通用·最准/最慢）", "mlx-community/whisper-large-v3-mlx", 3100),
    # 中文微调（BELLE）：针对普通话明显更准，会议/口语场景优先。punct 版自带标点，读起来更顺。
    ModelInfo("belle-v3-zh-punct", "transcribe", "Belle 中文微调 v3·带标点（会议推荐）", "mlx-community/belle-whisper-large-v3-zh-punct-fp16", 3080),
    ModelInfo("belle-v3-turbo-zh", "transcribe", "Belle 中文微调 v3·Turbo（快）", "mlx-community/belle-whisper-large-v3-turbo-zh-fp16", 1600),
    ModelInfo("pyannote-community-1", "diarize", "pyannote 说话人分离 community-1", "pyannote/speaker-diarization-community-1", 90),
]

_DEFAULT_ACTIVE = {"transcribe": "whisper-large-v3", "diarize": "pyannote-community-1"}


class ModelRegistry:
    def __init__(self, config_path: str,
                 is_downloaded_fn: Callable[[str], bool],
                 download_fn: Callable[[str], None]):
        self.config_path = Path(config_path)
        self._is_downloaded = is_downloaded_fn
        self._download = download_fn
        self._by_id = {m.id: m for m in AVAILABLE}
        self._state = {"active": dict(_DEFAULT_ACTIVE), "downloaded": []}
        if self.config_path.is_file():
            saved = json.loads(self.config_path.read_text(encoding="utf-8"))
            self._state["active"].update(saved.get("active", {}))
            self._state["downloaded"] = saved.get("downloaded", [])

    def _save(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _downloaded(self, m: ModelInfo) -> bool:
        return m.id in self._state["downloaded"] or self._is_downloaded(m.repo)

    def active(self, kind: str) -> str:
        return self._state["active"][kind]

    def active_repo(self, kind: str) -> str:
        """返回当前启用模型的 HF repo 路径。"""
        return self._by_id[self.active(kind)].repo

    def list_models(self) -> list[dict]:
        out = []
        for m in AVAILABLE:
            out.append({
                "id": m.id, "kind": m.kind, "display_name": m.display_name,
                "repo": m.repo, "size_mb": m.size_mb,
                "downloaded": self._downloaded(m),
                "active": self._state["active"].get(m.kind) == m.id,
            })
        return out

    def download(self, model_id: str) -> None:
        m = self._by_id[model_id]
        self._download(m.repo)
        if m.id not in self._state["downloaded"]:
            self._state["downloaded"].append(m.id)
        self._save()

    def set_active(self, model_id: str) -> None:
        m = self._by_id[model_id]
        self._state["active"][m.kind] = m.id
        self._save()
