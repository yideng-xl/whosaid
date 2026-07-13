from fastapi.testclient import TestClient
from transcribe_core.server import create_app
from transcribe_core.jobs import JobQueue
from transcribe_core.models import ModelRegistry
from transcribe_core.transcript import Segment
from transcribe_core.backend import InferenceBackend


class FakeBackend(InferenceBackend):
    id = "fake"
    def transcribe(self, audio_path, language, initial_prompt):
        return [Segment(0, 2, "你好"), Segment(2, 4, "在吗")]
    def diarize(self, audio_path, num_speakers):
        return [(0.0, 2.0, "SPEAKER_00"), (2.0, 4.0, "SPEAKER_01")]


class FailingBackend(InferenceBackend):
    """转写失败的后端，模拟模型未下载等异常情况"""
    id = "failing"
    def transcribe(self, audio_path, language, initial_prompt):
        raise RuntimeError("模型未下载")
    def diarize(self, audio_path, num_speakers):
        # 转写已失败，diarize 不会被调用到
        return []


def make_client(tmp_path, backend=None):
    if backend is None:
        backend = FakeBackend()
    reg = ModelRegistry(str(tmp_path / "config.json"),
                        is_downloaded_fn=lambda repo: True,
                        download_fn=lambda repo: None)
    app = create_app(JobQueue(backend), reg)
    return TestClient(app)


def test_submit_and_fetch_job(tmp_path):
    c = make_client(tmp_path)
    jid = c.post("/jobs", json={"audio_path": "/x/a.m4a"}).json()["job_id"]
    job = c.get(f"/jobs/{jid}").json()
    assert job["status"] == "done"
    assert "说话人A：你好" in job["txt"]


def test_rename_then_export(tmp_path):
    c = make_client(tmp_path)
    jid = c.post("/jobs", json={"audio_path": "/x/a.m4a"}).json()["job_id"]
    c.post(f"/jobs/{jid}/rename", json={"orig": "说话人A", "name": "张三"})
    txt = c.get(f"/jobs/{jid}/export", params={"fmt": "txt"}).text
    assert txt.startswith("张三：你好")


def test_models_list_and_switch(tmp_path):
    c = make_client(tmp_path)
    models = c.get("/models").json()
    assert any(m["id"] == "whisper-large-v3" and m["active"] for m in models)
    c.post("/models/active", json={"model_id": "whisper-small"})
    models = c.get("/models").json()
    assert any(m["id"] == "whisper-small" and m["active"] for m in models)


def test_rename_export_409_when_transcript_none(tmp_path):
    """测试当转写失败导致 transcript 为 None 时，rename 和 export 返回 409"""
    # 用转写失败的后端提交任务，job 会进入 failed 状态，transcript 保持 None
    c = make_client(tmp_path, backend=FailingBackend())
    jid = c.post("/jobs", json={"audio_path": "/x/a.m4a"}).json()["job_id"]

    # 验证任务已同步执行完，状态为 failed
    job = c.get(f"/jobs/{jid}").json()
    assert job["status"] == "failed"
    assert job["error"] == "模型未下载"

    # 验证 rename 返回 409
    resp_rename = c.post(f"/jobs/{jid}/rename", json={"orig": "说话人A", "name": "张三"})
    assert resp_rename.status_code == 409
    assert "转写未完成" in resp_rename.json()["detail"]

    # 验证 export 返回 409
    resp_export = c.get(f"/jobs/{jid}/export", params={"fmt": "txt"})
    assert resp_export.status_code == 409
    assert "转写未完成" in resp_export.json()["detail"]
