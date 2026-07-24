"""分离引擎工厂：按 repo 选具体引擎。"""
from __future__ import annotations

from .base import DiarizeEngine
from .pyannote_engine import PyannoteEngine


def make_engine(repo: str) -> DiarizeEngine:
    if repo.startswith("pyannote/"):
        return PyannoteEngine(repo)
    raise ValueError(f"未知分离引擎 repo: {repo}")
