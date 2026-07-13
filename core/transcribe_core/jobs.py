"""转写任务：串起 转写→分离→对齐→生成 Transcript，并推进度。"""
from __future__ import annotations

import itertools
import queue as _q
import threading
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
        self._subscribers: dict[str, list] = {}
        self._lock = threading.Lock()

    def submit(self, audio_path: str) -> str:
        jid = f"job{next(_ids)}"
        job = Job(id=jid, audio_path=audio_path, status="queued",
                  progress=0.0, transcript=None, error=None)
        self._jobs[jid] = job
        self.run_job(job, on_progress=lambda j: None)
        return jid

    def subscribe(self, job_id: str):
        """返回一个进度队列，`run_job` 每次进度更新都会往这里 put 一条消息。"""
        ch = _q.Queue()
        with self._lock:
            self._subscribers.setdefault(job_id, []).append(ch)
        return ch

    def submit_async(self, audio_path: str) -> str:
        """提交任务并立即返回 job_id，实际转写在后台线程执行，可通过 subscribe 拿进度。"""
        jid = f"job{next(_ids)}"
        job = Job(id=jid, audio_path=audio_path, status="queued",
                  progress=0.0, transcript=None, error=None)
        self._jobs[jid] = job

        def _emit(j: Job) -> None:
            with self._lock:
                for ch in self._subscribers.get(j.id, []):
                    ch.put({"status": j.status, "progress": j.progress, "error": j.error})

        threading.Thread(target=self.run_job, args=(job, _emit), daemon=True).start()
        return jid

    def run_job(self, job: Job, on_progress: Callable[[Job], None]) -> None:
        try:
            job.status = "running"
            on_progress(job)
            # TODO 单并发信号量：Task 9 在此包一个全局 threading.Semaphore(1)，
            # 串行化推理段（transcribe + diarize），避免多任务并发抢占本地算力。
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
