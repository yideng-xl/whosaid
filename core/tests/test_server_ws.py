from fastapi.testclient import TestClient
from transcribe_core.server import create_app
from transcribe_core.jobs import JobQueue
from transcribe_core.models import ModelRegistry
from transcribe_core.transcript import Segment
from transcribe_core.backend import InferenceBackend


class FakeBackend(InferenceBackend):
    id = "fake"
    def transcribe(self, audio_path, language, initial_prompt):
        return [Segment(0, 2, "你好")]
    def diarize(self, audio_path, num_speakers):
        return [(0.0, 2.0, "SPEAKER_00")]


def test_ws_streams_until_done(tmp_path):
    reg = ModelRegistry(str(tmp_path / "config.json"),
                        is_downloaded_fn=lambda r: True, download_fn=lambda r: None)
    c = TestClient(create_app(JobQueue(FakeBackend()), reg))
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
