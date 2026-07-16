"""本地转写服务：REST + WebSocket，把内核各组件装成可被 Tauri 外壳调用的服务。"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .jobs import JobQueue
from .models import ModelRegistry


class SubmitReq(BaseModel):
    audio_path: str
    num_speakers: int | None = None  # 用户预计说话人数，约束 pyannote 分离（缺省=自动）


class RenameReq(BaseModel):
    orig: str
    name: str


class NumSpeakersReq(BaseModel):
    num_speakers: int | None = None  # 预计说话人数;None=自动


class ActiveReq(BaseModel):
    model_id: str


def _start_parent_watchdog(poll_sec: float = 2.0) -> None:
    """盯住父进程：Tauri 外壳一旦退出（含被强杀/dev 重启），本服务会被 launchd 收养
    （getppid()==1），此时自我退出，避免残留孤儿进程占用内存与模型。仅当父进程真的消失
    才退出，父进程存活期间绝不误杀。"""
    import os
    import threading
    import time

    def _watch() -> None:
        while True:
            time.sleep(poll_sec)
            if os.getppid() == 1:  # 被 init/launchd 收养 → 已成孤儿
                os._exit(0)

    threading.Thread(target=_watch, daemon=True).start()


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
        return {"job_id": queue.submit_async(req.audio_path, req.num_speakers)}

    @app.get("/jobs")
    def list_jobs():
        return [
            {"id": j.id, "audio_path": j.audio_path, "status": j.status,
             "progress": j.progress, "error": j.error, "created_at": j.created_at}
            for j in queue.list()
        ]

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str):
        j = _job_or_404(job_id)
        done = j.status == "done"
        speakers = []
        if j.transcript is not None and done:
            # 去重保序地列出原始说话人标签及其当前显示名，供前端改名 UI 使用。
            # 用原始标签(seg.speaker)作为 rename 的 orig，才能反复改名（映射恒以原始标签为键）。
            seen = set()
            for seg in j.transcript.segments:
                orig = seg.speaker
                if orig is None or orig in seen:
                    continue
                seen.add(orig)
                speakers.append({"orig": orig, "name": j.transcript.display_speaker(seg)})
        if j.status in ("done", "failed", "paused", "queued"):
            phase = j.status
        else:  # running
            phase = "diarizing" if j.progress >= 0.85 else "transcribing"
        txt = ""
        if j.transcript is not None:
            txt = j.transcript.to_txt() if done else j.transcript.plain_text()
        # plain_txt：始终为无说话人分组的纯文字稿，供详情面板"①转文字"视图用；
        # 与 txt（done 时切换为分人稿 to_txt）语义独立，不随 done 变化。
        plain = j.transcript.plain_text() if j.transcript is not None else ""
        return {
            "id": j.id, "status": j.status, "progress": j.progress, "error": j.error,
            "total_chunks": j.total_chunks, "chunks_done": j.chunks_done,
            "phase": phase, "txt": txt, "plain_txt": plain, "speakers": speakers,
            "num_speakers": j.num_speakers,
        }

    @app.post("/jobs/{job_id}/pause")
    def pause(job_id: str):
        _job_or_404(job_id)
        if not queue.pause(job_id):
            raise HTTPException(409, "当前不可暂停（非转写阶段）")
        return {"ok": True}

    @app.post("/jobs/{job_id}/resume")
    def resume(job_id: str):
        _job_or_404(job_id)
        if not queue.resume(job_id):
            raise HTTPException(409, "当前不可继续")
        return {"ok": True}

    @app.delete("/jobs/{job_id}")
    def delete_job(job_id: str):
        j = _job_or_404(job_id)
        # running/queued 时后台线程仍在跑：直接 pop 既不会停线程，线程还会经 on_change=store.save
        # 把已删 JSON 复活，故仅允许终态（done/failed）与 paused 删除。
        if j.status in ("running", "queued"):
            raise HTTPException(409, "任务进行中，请先暂停或等待完成再删除")
        queue._jobs.pop(job_id, None)
        queue._pause.pop(job_id, None)
        if store is not None:
            store.delete(job_id)
        return {"ok": True}

    @app.post("/jobs/{job_id}/rename")
    def rename(job_id: str, req: RenameReq):
        j = _job_or_404(job_id)
        if j.transcript is None:
            raise HTTPException(409, "转写未完成")
        j.transcript.rename_speaker(req.orig, req.name)
        if store is not None:
            store.save(j)
        return {"ok": True}

    @app.post("/jobs/{job_id}/num_speakers")
    def set_num_speakers(job_id: str, req: NumSpeakersReq):
        _job_or_404(job_id)
        if not queue.set_num_speakers(job_id, req.num_speakers):
            raise HTTPException(409, "当前不可修改人数(已完成请用重新分人,或正在分人中)")
        return {"ok": True}

    @app.post("/jobs/{job_id}/rediarize")
    def rediarize(job_id: str, req: NumSpeakersReq):
        _job_or_404(job_id)
        if not queue.rediarize(job_id, req.num_speakers):
            raise HTTPException(409, "仅已完成的任务可重新分人")
        return {"ok": True}

    @app.get("/jobs/{job_id}/export", response_class=PlainTextResponse)
    def export(job_id: str, fmt: str = "txt"):
        j = _job_or_404(job_id)
        if j.transcript is None:
            raise HTTPException(409, "转写未完成")
        if fmt == "srt":
            return j.transcript.to_srt()
        return j.transcript.to_txt()

    @app.get("/jobs/{job_id}/speaker_sample")
    def speaker_sample(job_id: str, spk: str):
        j = _job_or_404(job_id)
        if j.transcript is None:
            raise HTTPException(409, "转写未完成")
        from .backend import sample_ranges
        from .audio import extract_samples_concat
        # 取该说话人 3 段较长发言拼接：单段常混音难辨，多段更好判断是谁
        ranges = sample_ranges(j.transcript.segments, spk)
        if not ranges:
            raise HTTPException(404, "该说话人不存在")
        data = extract_samples_concat(j.audio_path, ranges)
        return Response(content=data, media_type="audio/mpeg")

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
            # 客户端可能已先行发起关闭（例如收到终态帧后主动断开），此时再次 close 会触发
            # "Cannot call send once a close message has been sent"，故先判连接态。
            from starlette.websockets import WebSocketState
            if websocket.client_state == WebSocketState.CONNECTED:
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
        # pyannote 类模型无 config.json（用 config.yaml），任一 marker 命中即视为已下载
        for marker in ("config.json", "config.yaml"):
            if try_to_load_from_cache(repo, marker) is not None:
                return True
        return False

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
    _start_parent_watchdog()  # 外壳退出即自杀，杜绝孤儿服务
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
