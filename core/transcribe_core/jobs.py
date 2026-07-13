"""转写任务：串起 转写→分离→对齐→生成 Transcript，并推进度。"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Callable

from .backend import InferenceBackend, align
from .transcript import Transcript

_ids = itertools.count(1)


@dataclass
class Job:
    id: str
    audio_path: str
    status: str          # queued | running | done | failed
    progress: float
    transcript: Transcript | None
    error: str | None


class JobQueue:
    def __init__(self, backend: InferenceBackend, language: str | None = "zh",
                 prompt: str | None = None, num_speakers: int | None = None):
        self.backend = backend
        self.language = language
        self.prompt = prompt
        self.num_speakers = num_speakers
        self._jobs: dict[str, Job] = {}

    def submit(self, audio_path: str) -> str:
        jid = f"job{next(_ids)}"
        job = Job(id=jid, audio_path=audio_path, status="queued",
                  progress=0.0, transcript=None, error=None)
        self._jobs[jid] = job
        self.run_job(job, on_progress=lambda j: None)
        return jid

    def run_job(self, job: Job, on_progress: Callable[[Job], None]) -> None:
        try:
            job.status = "running"
            on_progress(job)
            segs = self.backend.transcribe(job.audio_path, self.language, self.prompt)
            job.progress = 0.5
            on_progress(job)
            turns = self.backend.diarize(job.audio_path, self.num_speakers)
            job.progress = 0.9
            on_progress(job)
            labeled = align(segs, turns)
            job.transcript = Transcript(segments=labeled)
            job.progress = 1.0
            job.status = "done"
            on_progress(job)
        except Exception as e:  # 异常不外泄，写入 job
            job.status = "failed"
            job.error = str(e)
            on_progress(job)

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        return list(self._jobs.values())
