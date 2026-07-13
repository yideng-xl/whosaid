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


def make_client(tmp_path):
    reg = ModelRegistry(str(tmp_path / "config.json"),
                        is_downloaded_fn=lambda repo: True,
                        download_fn=lambda repo: None)
    app = create_app(JobQueue(FakeBackend()), reg)
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
