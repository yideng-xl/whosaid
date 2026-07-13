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

# 全局单并发闸门：本机算力/显存有限，任意时刻至多一个 run_job 处于推理段
# （transcribe + diarize），避免多任务并发同时抢占本地模型资源。
_infer_gate = threading.Semaphore(1)


@dataclass
class Job:
    id: str
    audio_path: str
    status: str          # queued | running | done | failed
    progress: float
    transcript: Transcript | None
    error: str | None


class JobQueue:
    def __init__(self, backend: InferenceBackend | None = None, *,
                 backend_factory: Callable[[str, str], InferenceBackend] | None = None,
                 registry=None,
                 language: str | None = "zh",
                 prompt: str | None = None, num_speakers: int | None = None):
        # backend：固定后端（向后兼容原有调用方式）
        # backend_factory + registry：按“当前启用模型”动态构造后端，
        #   优先于固定 backend——用于让模型切换在下一个任务生效
        self.backend = backend
        self._backend_factory = backend_factory
        self._registry = registry
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

    def unsubscribe(self, job_id: str, ch) -> None:
        """摘除某个订阅通道（job 达终态或 WS 连接结束时调用），避免 _subscribers 无界增长。"""
        with self._lock:
            chans = self._subscribers.get(job_id)
            if not chans:
                return
            if ch in chans:
                chans.remove(ch)
            if not chans:
                del self._subscribers[job_id]

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
        with _infer_gate:  # 串行化推理段，任意时刻至多一个任务在跑
            try:
                # 解析本次实际使用的 backend：有 factory+registry 时按当前启用模型现构，
                # 确保切换模型后下一个任务立即生效；否则退回构造时传入的固定 backend
                backend = self.backend
                if self._backend_factory is not None and self._registry is not None:
                    backend = self._backend_factory(
                        self._registry.active_repo("transcribe"),
                        self._registry.active_repo("diarize"),
                    )
                job.status = "running"
                on_progress(job)
                segs = backend.transcribe(job.audio_path, self.language, self.prompt)
                job.progress = 0.5
                on_progress(job)
                turns = backend.diarize(job.audio_path, self.num_speakers)
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
