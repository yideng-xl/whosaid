import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from transcribe_core.server import create_app
from transcribe_core.jobs import JobQueue
from transcribe_core.models import ModelRegistry
from transcribe_core.transcript import Segment
from transcribe_core.backend import InferenceBackend


def _wait_for(cond, timeout=2.0):
    """轮询直到条件成立或超时；用于等待服务端异步收尾（unsubscribe/close）完成。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return False


class FakeBackend(InferenceBackend):
    id = "fake"
    def transcribe(self, audio_path, language, initial_prompt):
        return [Segment(0, 2, "你好")]
    def diarize(self, audio_path, num_speakers):
        return [(0.0, 2.0, "SPEAKER_00")]


def test_ws_streams_until_done(tmp_path):
    reg = ModelRegistry(str(tmp_path / "config.json"),
                        is_downloaded_fn=lambda r: True, download_fn=lambda r: None)
    c = TestClient(create_app(JobQueue(FakeBackend(), duration_fn=lambda p: 1.0, extract_fn=lambda src, start, dur: src), reg))
    jid = c.post("/jobs", json={"audio_path": "/x/a.m4a"}).json()["job_id"]
    with c.websocket_connect(f"/ws/jobs/{jid}") as ws:
        last = None
        while True:
            msg = ws.receive_json()
            last = msg
            if msg["status"] in ("done", "failed"):
                break
        assert last["status"] == "done"
        assert last["progress"] == 1.0


def test_ws_unsubscribes_on_disconnect(tmp_path):
    """job 达终态、连接结束后，_subscribers 里不应残留该 job_id 的订阅通道。"""
    reg = ModelRegistry(str(tmp_path / "config.json"),
                        is_downloaded_fn=lambda r: True, download_fn=lambda r: None)
    queue = JobQueue(FakeBackend(), duration_fn=lambda p: 1.0, extract_fn=lambda src, start, dur: src)
    c = TestClient(create_app(queue, reg))
    jid = c.post("/jobs", json={"audio_path": "/x/a.m4a"}).json()["job_id"]
    with c.websocket_connect(f"/ws/jobs/{jid}") as ws:
        while True:
            msg = ws.receive_json()
            if msg["status"] in ("done", "failed"):
                break
    assert _wait_for(lambda: not queue._subscribers.get(jid))


def test_ws_invalid_job_id_closes_immediately(tmp_path):
    """job_id 不存在时，服务端应直接关闭连接，不应挂起等待永远不会到来的进度。"""
    reg = ModelRegistry(str(tmp_path / "config.json"),
                        is_downloaded_fn=lambda r: True, download_fn=lambda r: None)
    c = TestClient(create_app(JobQueue(FakeBackend(), duration_fn=lambda p: 1.0, extract_fn=lambda src, start, dur: src), reg))
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws/jobs/no-such-job") as ws:
            ws.receive_json()
