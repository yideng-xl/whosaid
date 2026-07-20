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
                 download_fn: Callable[[str], None],
                 delete_fn: Callable[[str], None] | None = None):
        self.config_path = Path(config_path)
        self._is_downloaded = is_downloaded_fn
        self._download = download_fn
        self._delete = delete_fn  # 可选：不传时 delete() 直接报错，不静默跳过
        self._by_id = {m.id: m for m in AVAILABLE}
        self._state = {
            "active": dict(_DEFAULT_ACTIVE), "downloaded": [],
            "hf_token": None, "hf_endpoint": None,
        }
        if self.config_path.is_file():
            saved = json.loads(self.config_path.read_text(encoding="utf-8"))
            self._state["active"].update(saved.get("active", {}))
            self._state["downloaded"] = saved.get("downloaded", [])
            self._state["hf_token"] = saved.get("hf_token")
            self._state["hf_endpoint"] = saved.get("hf_endpoint")

    def _save(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _downloaded(self, m: ModelInfo) -> bool:
        # 以本地缓存为唯一权威：config.json 里的 downloaded 列表只是记账，
        # 缓存被外部清掉/换了 HF_HOME 时不能拿它盖住真相，否则前端显示「已下载」，
        # 直到转写时才炸在 mlx_backend 的 local_files_only 上。
        return self._is_downloaded(m.repo)

    def active(self, kind: str) -> str:
        return self._state["active"][kind]

    def active_repo(self, kind: str) -> str:
        """返回当前启用模型的 HF repo 路径。"""
        return self._by_id[self.active(kind)].repo

    def info(self, model_id: str) -> dict:
        """返回指定模型的静态信息（repo/size_mb 等），供下载进度查询等只读场景使用，
        不经 self._by_id 之外暴露内部结构。model_id 不存在时抛 KeyError（与 download/set_active 一致）。"""
        m = self._by_id[model_id]
        return {"id": m.id, "kind": m.kind, "display_name": m.display_name,
                "repo": m.repo, "size_mb": m.size_mb}

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

    def delete(self, model_id: str) -> None:
        """删除已下载模型：清掉本地缓存 + 从 downloaded 列表移除 + 持久化。
        model_id 不存在时按 AVAILABLE 查找抛 KeyError（与 download/set_active 行为一致）；
        未注入 delete_fn（服务端未妥善配置）时显式抛 RuntimeError，避免静默假装已删除。"""
        m = self._by_id[model_id]
        if self._delete is None:
            raise RuntimeError("delete_fn 未配置，无法删除模型缓存")
        self._delete(m.repo)
        if m.id in self._state["downloaded"]:
            self._state["downloaded"].remove(m.id)
        self._save()

    def set_active(self, model_id: str) -> None:
        m = self._by_id[model_id]
        self._state["active"][m.kind] = m.id
        self._save()

    def get_settings(self) -> dict:
        return {
            "hf_token": self._state.get("hf_token"),
            "hf_endpoint": self._state.get("hf_endpoint"),
        }

    def set_settings(self, hf_token: str | None, hf_endpoint: str | None) -> None:
        # 空字符串按未设置处理，避免前端清空输入框后仍把 "" 当有效 token/endpoint 存下来
        self._state["hf_token"] = hf_token or None
        self._state["hf_endpoint"] = hf_endpoint or None
        self._save()
