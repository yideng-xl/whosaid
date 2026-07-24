"""分离引擎抽象：输入音频路径，输出说话人轮次 list[(start,end,spk)]。"""
from __future__ import annotations

from abc import ABC, abstractmethod

Turn = tuple[float, float, str]


class DiarizeEngine(ABC):
    """说话人分离引擎接口。二期可新增 sherpa 等实现，接入 make_engine 工厂即可。"""

    @abstractmethod
    def diarize(self, audio_path: str, num_speakers: int | None) -> list[Turn]:
        """返回说话人时间段 [(start, end, raw_speaker), ...]。"""
        ...
