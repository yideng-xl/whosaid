"""转写任务：串起 转写→分离→对齐→生成 Transcript，并推进度。"""
from __future__ import annotations

import itertools
import os
import queue as _q
import threading
from dataclasses import dataclass
from typing import Callable

from .backend import InferenceBackend, align
from .chunking import plan_chunks, offset_segments
from .transcript import Transcript

_ids = itertools.count(1)

# 全局单并发闸门：本机算力/显存有限，任意时刻至多一个 run_job 处于推理段
# （transcribe + diarize），避免多任务并发同时抢占本地模型资源。
_infer_gate = threading.Semaphore(1)


@dataclass
class Job:
    id: str
    audio_path: str
    status: str          # queued | running | paused | done | failed
    progress: float
    transcript: Transcript | None
    error: str | None
    total_chunks: int = 0
    chunks_done: int = 0


class JobQueue:
    def __init__(self, backend: InferenceBackend | None = None, *,
                 backend_factory: Callable[[str, str], InferenceBackend] | None = None,
                 registry=None,
                 language: str | None = "zh",
                 prompt: str | None = None, num_speakers: int | None = None,
                 on_change: Callable[[Job], None] | None = None,
                 duration_fn: Callable[[str], float] | None = None,
                 extract_fn: Callable[[str, float, float], str] | None = None,
                 chunk_sec: float = 120.0):
        # backend：固定后端（向后兼容原有调用方式）
        # backend_factory + registry：按"当前启用模型"动态构造后端，
        #   优先于固定 backend——用于让模型切换在下一个任务生效
        self.backend = backend
        self._backend_factory = backend_factory
        self._registry = registry
        self.language = language
        self.prompt = prompt
        self.num_speakers = num_speakers
        self._on_change = on_change
        # duration_fn/extract_fn：可注入的音频 IO 钩子（便于测试；默认惰性 import 真实实现）
        self._duration_fn = duration_fn
        self._extract_fn = extract_fn
        self._chunk_sec = chunk_sec
        self._jobs: dict[str, Job] = {}
        self._subscribers: dict[str, list] = {}
        self._lock = threading.Lock()
        # 每个 job 一个暂停标志位；pause() 置位，run_job 在块边界检查
        self._pause: dict[str, threading.Event] = {}

    def _new_id(self) -> str:
        """跳过已存在 id（preload 历史 job 后，避免全局计数器从头产生碰撞）"""
        while True:
            jid = f"job{next(_ids)}"
            if jid not in self._jobs:
                return jid

    def _notify(self, job: Job) -> None:
        """状态变化时调用 on_change 钩子（如已设置）"""
        if self._on_change is not None:
            self._on_change(job)

    def _emit(self, j: Job) -> None:
        """把进度推给该 job 的所有订阅通道（WS 等）。submit_async 与 resume 共用。"""
        with self._lock:
            for ch in self._subscribers.get(j.id, []):
                ch.put({"status": j.status, "progress": j.progress, "error": j.error})

    def _duration(self, path: str) -> float:
        """拿音频总时长；未注入时惰性 import 真实实现（避免测试环境无 ffmpeg 时 import 失败）。"""
        if self._duration_fn is not None:
            return self._duration_fn(path)
        from .audio import probe_duration
        return probe_duration(path)

    def _extract(self, src: str, start: float, dur: float) -> str:
        """切一块临时 wav；未注入时惰性 import 真实实现。"""
        if self._extract_fn is not None:
            return self._extract_fn(src, start, dur)
        from .audio import extract_wav
        return extract_wav(src, start, dur)

    def preload(self, jobs: list[Job]) -> None:
        """预载历史 job 到队列（用于恢复持久化状态）。加锁避免与后台 submit_async 竞态。"""
        with self._lock:
            for j in jobs:
                self._jobs[j.id] = j

    def submit(self, audio_path: str) -> str:
        jid = self._new_id()
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
        jid = self._new_id()
        job = Job(id=jid, audio_path=audio_path, status="queued",
                  progress=0.0, transcript=None, error=None)
        self._jobs[jid] = job
        self._notify(job)

        threading.Thread(target=self.run_job, args=(job, self._emit), daemon=True).start()
        return jid

    def run_job(self, job: Job, on_progress: Callable[[Job], None]) -> None:
        with _infer_gate:  # 串行化推理段；暂停 return 即释放，CPU 真正空下来
            try:
                backend = self.backend
                if self._backend_factory is not None and self._registry is not None:
                    backend = self._backend_factory(
                        self._registry.active_repo("transcribe"),
                        self._registry.active_repo("diarize"),
                    )
                job.status = "running"
                on_progress(job); self._notify(job)

                # 首次进入：算总块数（累加 transcript 惰性初始化，见循环内注释）
                if job.total_chunks == 0:
                    duration = self._duration(job.audio_path)
                    job.total_chunks = len(plan_chunks(duration, self._chunk_sec)) or 1

                chunks = plan_chunks(self._duration(job.audio_path), self._chunk_sec)
                # 从断点续：只跑 chunks_done 及之后的块
                for index, start, dur in chunks[job.chunks_done:]:
                    # 每次迭代都重新取暂停标志（不能在循环外缓存一次）：
                    # pause() 可能在本次 run_job 启动之后才创建/置位该 Event，
                    # 若在循环外只取一次，续跑线程里拿到的会是旧引用（甚至是 None），
                    # 导致后续块检测不到暂停请求而把整个任务跑完。
                    pause_ev = self._pause.get(job.id)
                    if pause_ev is not None and pause_ev.is_set():
                        job.status = "paused"
                        on_progress(job); self._notify(job)   # 存盘（on_change=store.save）
                        return
                    wav = self._extract(job.audio_path, start, dur)
                    try:
                        segs = backend.transcribe(wav, self.language, self.prompt)
                    finally:
                        if self._extract_fn is None:
                            os.remove(wav)  # 真实临时文件才删；注入的假路径不删
                    # 惰性初始化：只在第一块真正转成功后才创建 transcript。
                    # 若在循环外提前初始化，第一块转写就失败时 transcript 会变成"非
                    # None 的空对象"，破坏 server.py "transcript is None ⇒ 未完成/
                    # 不可用" 的 409 判断契约；而失败前已转完的块要保留（支持从
                    # failed 状态 resume 续跑，不丢已转部分）。
                    if job.transcript is None:
                        job.transcript = Transcript(segments=[])
                    job.transcript.segments.extend(offset_segments(segs, start))
                    job.chunks_done = index + 1
                    job.progress = 0.85 * job.chunks_done / job.total_chunks
                    on_progress(job); self._notify(job)

                # 全部转完 → 整段分离（不可暂停）→ 对齐
                job.progress = 0.85
                on_progress(job)
                turns = backend.diarize(job.audio_path, self.num_speakers)
                job.progress = 0.95
                on_progress(job)
                labeled = align(job.transcript.segments, turns)
                job.transcript = Transcript(segments=labeled)
                job.progress = 1.0
                job.status = "done"
                on_progress(job); self._notify(job)
            except Exception as e:  # 异常不外泄，写入 job
                job.status = "failed"
                job.error = str(e)
                on_progress(job); self._notify(job)

    def pause(self, job_id: str) -> bool:
        """请求暂停：仅对运行中且处于转写阶段(progress<0.85)有效。返回是否被接受。"""
        job = self._jobs.get(job_id)
        if job is None or job.status != "running" or job.progress >= 0.85:
            return False
        self._pause.setdefault(job_id, threading.Event()).set()
        return True

    def resume(self, job_id: str) -> bool:
        """从断点续跑：仅 paused/failed 可续。"""
        job = self._jobs.get(job_id)
        if job is None or job.status not in ("paused", "failed"):
            return False
        ev = self._pause.get(job_id)
        if ev is not None:
            ev.clear()
        threading.Thread(target=self.run_job, args=(job, self._emit), daemon=True).start()
        return True

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        return list(self._jobs.values())
