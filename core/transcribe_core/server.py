"""本地转写服务：REST + WebSocket，把内核各组件装成可被 Tauri 外壳调用的服务。"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .jobs import JobQueue
from .models import ModelRegistry


class SubmitReq(BaseModel):
    audio_path: str


class RenameReq(BaseModel):
    orig: str
    name: str


class ActiveReq(BaseModel):
    model_id: str


def create_app(queue: JobQueue, registry: ModelRegistry, store=None) -> FastAPI:
    app = FastAPI(title="本地转写服务")
    # 桌面外壳(Tauri)的前端页面来自 localhost:1420/tauri://，与本服务(127.0.0.1:随机端口)
    # 跨域。本服务仅监听回环、单机自用，放开所有来源即可，否则 webview 的 fetch 会被 CORS 拦成
    # "Load failed"。不使用凭据，故 allow_credentials=False（与 allow_origins=* 兼容）。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _job_or_404(job_id: str):
        job = queue.get(job_id)
        if job is None:
            raise HTTPException(404, "job 不存在")
        return job

    @app.post("/jobs")
    def submit(req: SubmitReq):
        # 用 submit_async 而非 submit：后台线程执行，接口立即返回，配合 WS 拿流式进度
        return {"job_id": queue.submit_async(req.audio_path)}

    @app.get("/jobs")
    def list_jobs():
        return [
            {"id": j.id, "audio_path": j.audio_path, "status": j.status, "progress": j.progress, "error": j.error}
            for j in queue.list()
        ]

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str):
        j = _job_or_404(job_id)
        speakers = []
        if j.transcript is not None:
            # 去重保序地列出原始说话人标签及其当前显示名，供前端改名 UI 使用。
            # 用原始标签(seg.speaker)作为 rename 的 orig，才能反复改名（映射恒以原始标签为键）。
            seen = set()
            for seg in j.transcript.segments:
                orig = seg.speaker
                if orig is None or orig in seen:
                    continue
                seen.add(orig)
                speakers.append({"orig": orig, "name": j.transcript.display_speaker(seg)})
        return {
            "id": j.id, "status": j.status, "progress": j.progress, "error": j.error,
            "txt": j.transcript.to_txt() if j.transcript else "",
            "speakers": speakers,
        }

    @app.post("/jobs/{job_id}/rename")
    def rename(job_id: str, req: RenameReq):
        j = _job_or_404(job_id)
        if j.transcript is None:
            raise HTTPException(409, "转写未完成")
        j.transcript.rename_speaker(req.orig, req.name)
        if store is not None:
            store.save(j)
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

    @app.websocket("/ws/jobs/{job_id}")
    async def ws_job(websocket: WebSocket, job_id: str):
        await websocket.accept()
        job = queue.get(job_id)
        if job is None:
            # job 不存在：直接关闭，不订阅、不进入 run_in_threadpool(ch.get) 死等
            await websocket.close()
            return
        ch = queue.subscribe(job_id)
        try:
            # 先推一次当前状态（可能已完成）
            await websocket.send_json({"status": job.status, "progress": job.progress, "error": job.error})
            if job.status in ("done", "failed"):
                return
            while True:
                msg = await run_in_threadpool(ch.get)
                await websocket.send_json(msg)
                if msg["status"] in ("done", "failed"):
                    break
        except WebSocketDisconnect:
            pass
        finally:
            # job 达终态或连接结束（含客户端主动断开）都要摘除订阅通道，避免 _subscribers 无界增长
            queue.unsubscribe(job_id, ch)
            await websocket.close()

    return app


def main() -> None:  # 生产入口：注入真实 MlxBackend，随机端口，端口号打到 stdout 供外壳读取
    import os
    import socket
    import uvicorn

    from .mlx_backend import MlxBackend
    from .store import JobStore
    from huggingface_hub import try_to_load_from_cache  # 判断模型是否已在本地缓存

    def is_downloaded(repo: str) -> bool:
        return try_to_load_from_cache(repo, "config.json") is not None

    def download(repo: str) -> None:
        from huggingface_hub import snapshot_download
        snapshot_download(repo)

    registry = ModelRegistry("config.json", is_downloaded, download)
    store = JobStore(os.environ.get("WHOSAID_DATA_DIR", "."))
    # 注入 backend_factory + registry：每个任务开跑前按"当前启用模型"现构后端，
    # 使 /models/active 切换的模型能在下一个任务真正生效（而非固定在启动时的 large-v3）
    queue = JobQueue(
        backend_factory=lambda whisper_repo, diarize_repo: MlxBackend(whisper_repo, diarize_repo),
        registry=registry,
        on_change=store.save,
    )
    queue.preload(store.load_all())
    app = create_app(queue, registry, store)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    print(f"PORT={port}", flush=True)  # Tauri 读取此行得知端口
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
