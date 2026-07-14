"""任务/转写稿的轻量持久化：落 JSON 到数据目录，启动读回。单用户桌面场景，不引入数据库。"""
from __future__ import annotations

import json
from pathlib import Path

from .jobs import Job
from .transcript import Transcript


class JobStore:
    def __init__(self, data_dir: str):
        self.dir = Path(data_dir) / "jobs"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, job_id: str) -> Path:
        return self.dir / f"{job_id}.json"

    def save(self, job: Job) -> None:
        d = {
            "id": job.id, "audio_path": job.audio_path, "status": job.status,
            "progress": job.progress, "error": job.error,
            "total_chunks": job.total_chunks, "chunks_done": job.chunks_done,
            "created_at": job.created_at, "num_speakers": job.num_speakers,
            "transcript": job.transcript.to_dict() if job.transcript else None,
        }
        self._path(job.id).write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")

    def delete(self, job_id: str) -> None:
        self._path(job_id).unlink(missing_ok=True)

    def load_all(self) -> list[Job]:
        jobs: list[Job] = []
        for p in sorted(self.dir.glob("*.json")):
            d = json.loads(p.read_text(encoding="utf-8"))
            status, err = d["status"], d.get("error")
            # 运行中/排队中无稳定断点，重启改判失败；paused 有断点，保留可续传
            if status in ("queued", "running"):
                status, err = "failed", "应用中断，请重新提交"
            t = Transcript.from_dict(d["transcript"]) if d.get("transcript") else None
            # 旧任务无 created_at：回退用 json 文件的修改时间，保证分组时间大致合理
            created_at = d.get("created_at") or p.stat().st_mtime
            jobs.append(Job(id=d["id"], audio_path=d["audio_path"], status=status,
                            progress=d["progress"], transcript=t, error=err,
                            total_chunks=d.get("total_chunks", 0),
                            chunks_done=d.get("chunks_done", 0),
                            created_at=created_at,
                            num_speakers=d.get("num_speakers")))
        return jobs
