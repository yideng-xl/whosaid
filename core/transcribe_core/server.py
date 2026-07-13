"""本地转写服务：REST + WebSocket，把内核各组件装成可被 Tauri 外壳调用的服务。"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from .jobs import JobQueue
from .models import ModelRegistry


class SubmitReq(BaseModel):
    audio_path: str


class RenameReq(BaseModel):
    orig: str
    name: str


class ActiveReq(BaseModel):
    model_id: str


def create_app(queue: JobQueue, registry: ModelRegistry) -> FastAPI:
    app = FastAPI(title="本地转写服务")

    def _job_or_404(job_id: str):
        job = queue.get(job_id)
        if job is None:
            raise HTTPException(404, "job 不存在")
        return job

    @app.post("/jobs")
    def submit(req: SubmitReq):
        return {"job_id": queue.submit(req.audio_path)}

    @app.get("/jobs")
    def list_jobs():
        return [
            {"id": j.id, "status": j.status, "progress": j.progress, "error": j.error}
            for j in queue.list()
        ]

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str):
        j = _job_or_404(job_id)
        return {
            "id": j.id, "status": j.status, "progress": j.progress, "error": j.error,
            "txt": j.transcript.to_txt() if j.transcript else "",
        }

    @app.post("/jobs/{job_id}/rename")
    def rename(job_id: str, req: RenameReq):
        j = _job_or_404(job_id)
        if j.transcript is None:
            raise HTTPException(409, "转写未完成")
        j.transcript.rename_speaker(req.orig, req.name)
        return {"ok": True}

    @app.get("/jobs/{job_id}/export", response_class=PlainTextResponse)
    def export(job_id: str, fmt: str = "txt"):
        j = _job_or_404(job_id)
        if j.transcript is None:
            raise HTTPException(409, "转写未完成")
        if fmt == "srt":
            return j.transcript.to_srt()
        return j.transcript.to_txt()

    @app.get("/models")
    def list_models():
        return registry.list_models()

    @app.post("/models/{model_id}/download")
    def download(model_id: str):
        registry.download(model_id)
        return {"ok": True}

    @app.post("/models/active")
    def set_active(req: ActiveReq):
        registry.set_active(req.model_id)
        return {"ok": True}

    return app


def main() -> None:  # 生产入口：注入真实 MlxBackend，随机端口，端口号打到 stdout 供外壳读取
    import socket
    import uvicorn

    from .mlx_backend import MlxBackend
    from huggingface_hub import try_to_load_from_cache  # 判断模型是否已在本地缓存

    def is_downloaded(repo: str) -> bool:
        return try_to_load_from_cache(repo, "config.json") is not None

    def download(repo: str) -> None:
        from huggingface_hub import snapshot_download
        snapshot_download(repo)

    registry = ModelRegistry("config.json", is_downloaded, download)
    queue = JobQueue(MlxBackend())
    app = create_app(queue, registry)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    print(f"PORT={port}", flush=True)  # Tauri 读取此行得知端口
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
