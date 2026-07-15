import time

from fastapi.testclient import TestClient
from transcribe_core.server import create_app
from transcribe_core.jobs import JobQueue
from transcribe_core.models import ModelRegistry
from transcribe_core.transcript import Segment
from transcribe_core.backend import InferenceBackend


def _wait_done(c, jid):
    """POST /jobs 改为后台线程异步执行后，需轮询到终态再断言（避免读到 running/queued 中间态）。"""
    for _ in range(50):
        j = c.get(f"/jobs/{jid}").json()
        if j["status"] in ("done", "failed"):
            return j
        time.sleep(0.02)
    raise AssertionError("job 未在预期时间内完成")


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
    app = create_app(JobQueue(backend, duration_fn=lambda p: 1.0,
                               extract_fn=lambda src, start, dur: src), reg)
    return TestClient(app)


def test_submit_and_fetch_job(tmp_path):
    c = make_client(tmp_path)
    jid = c.post("/jobs", json={"audio_path": "/x/a.m4a"}).json()["job_id"]
    job = _wait_done(c, jid)
    assert job["status"] == "done"
    assert "说话人A：你好" in job["txt"]


def test_rename_then_export(tmp_path):
    c = make_client(tmp_path)
    jid = c.post("/jobs", json={"audio_path": "/x/a.m4a"}).json()["job_id"]
    _wait_done(c, jid)
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


def test_get_job_returns_speakers_with_orig_and_display(tmp_path):
    c = make_client(tmp_path)
    jid = c.post("/jobs", json={"audio_path": "/x/a.m4a"}).json()["job_id"]
    _wait_done(c, jid)
    # 改名前：speakers 用原始标签，name 等于原始标签
    spk = c.get(f"/jobs/{jid}").json()["speakers"]
    assert {s["orig"] for s in spk} == {"说话人A", "说话人B"}
    assert all(s["orig"] == s["name"] for s in spk)
    # 改名后：orig 不变（仍是原始标签），name 变为真名，保证可反复改名
    c.post(f"/jobs/{jid}/rename", json={"orig": "说话人A", "name": "张三"})
    spk2 = {s["orig"]: s["name"] for s in c.get(f"/jobs/{jid}").json()["speakers"]}
    assert spk2["说话人A"] == "张三"


def test_rename_export_409_when_transcript_none(tmp_path):
    """测试当转写失败导致 transcript 为 None 时，rename 和 export 返回 409"""
    # 用转写失败的后端提交任务，job 会进入 failed 状态，transcript 保持 None
    c = make_client(tmp_path, backend=FailingBackend())
    jid = c.post("/jobs", json={"audio_path": "/x/a.m4a"}).json()["job_id"]

    # 后台线程执行完（轮询到终态），状态为 failed
    job = _wait_done(c, jid)
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


def test_list_jobs_includes_audio_path(tmp_path):
    """GET /jobs 返回列表项中含 audio_path 字段"""
    c = make_client(tmp_path)
    jid = c.post("/jobs", json={"audio_path": "/x/会议.m4a"}).json()["job_id"]
    _wait_done(c, jid)
    jobs = c.get("/jobs").json()
    assert any(j["id"] == jid and j["audio_path"] == "/x/会议.m4a" for j in jobs)


def test_delete_removes_job_and_file(tmp_path):
    from transcribe_core.server import create_app
    from transcribe_core.jobs import JobQueue
    from transcribe_core.store import JobStore
    from transcribe_core.models import ModelRegistry
    from fastapi.testclient import TestClient
    store = JobStore(str(tmp_path / "data"))
    reg = ModelRegistry(str(tmp_path / "c.json"), is_downloaded_fn=lambda r: True, download_fn=lambda r: None)
    q = JobQueue(FakeBackend(), on_change=store.save)
    c = TestClient(create_app(q, reg, store))
    jid = c.post("/jobs", json={"audio_path": "/x/a.m4a"}).json()["job_id"]
    _wait_done(c, jid)
    assert c.delete(f"/jobs/{jid}").json()["ok"] is True
    assert all(j["id"] != jid for j in c.get("/jobs").json())
    assert JobStore(str(tmp_path / "data"))._path(jid).exists() is False


def test_delete_running_job_returns_409(tmp_path):
    """running/queued 的 job 后台线程仍在跑：删除应被拒绝（409），避免线程存活期间 job 被摘除、
    且经 on_change=store.save 把已删 JSON 复活。"""
    from transcribe_core.jobs import Job
    from transcribe_core.store import JobStore
    store = JobStore(str(tmp_path / "data"))
    reg = ModelRegistry(str(tmp_path / "c.json"), is_downloaded_fn=lambda r: True, download_fn=lambda r: None)
    q = JobQueue(FakeBackend(), on_change=store.save)
    c = TestClient(create_app(q, reg, store))
    jid = "job_running"
    # 直接构造一个处于 running 态的 job（绕开真实后台线程，专注测服务端状态校验）
    q._jobs[jid] = Job(id=jid, audio_path="/x/a.m4a", status="running", progress=0.5,
                        transcript=None, error=None)
    resp = c.delete(f"/jobs/{jid}")
    assert resp.status_code == 409
    assert q.get(jid) is not None  # 未被摘除


def test_pause_resume_endpoints_return_ok_or_409(tmp_path):
    c = make_client(tmp_path)
    jid = c.post("/jobs", json={"audio_path": "/x/a.m4a"}).json()["job_id"]
    _wait_done(c, jid)
    # 已完成的任务不可暂停 → 409
    assert c.post(f"/jobs/{jid}/pause").status_code == 409


def test_get_job_includes_chunk_and_phase(tmp_path):
    c = make_client(tmp_path)
    jid = c.post("/jobs", json={"audio_path": "/x/a.m4a"}).json()["job_id"]
    _wait_done(c, jid)
    d = c.get(f"/jobs/{jid}").json()
    assert "total_chunks" in d and "chunks_done" in d
    assert d["phase"] == "done"


def test_speaker_sample_returns_audio(tmp_path):
    import os, pytest
    clip = os.path.expanduser("~/Workspace/develop/oneworkspace/local-ai/radio/测试短音频-8秒.m4a")
    if not os.path.exists(clip):
        pytest.skip("缺测试音频")
    c = make_client(tmp_path)
    jid = c.post("/jobs", json={"audio_path": clip}).json()["job_id"]
    _wait_done(c, jid)
    spk = c.get(f"/jobs/{jid}").json()["speakers"][0]["orig"]
    r = c.get(f"/jobs/{jid}/speaker_sample", params={"spk": spk})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/")
    assert len(r.content) > 0

test_speaker_sample_returns_audio = __import__("pytest").mark.slow(test_speaker_sample_returns_audio)


def test_ws_job_closes_cleanly_after_done(tmp_path):
    """已完成的 job：连上应收到一帧终态后正常关闭，不抛出连接态竞态异常。"""
    c = make_client(tmp_path)
    jid = c.post("/jobs", json={"audio_path": "/x/a.m4a"}).json()["job_id"]
    _wait_done(c, jid)
    with c.websocket_connect(f"/ws/jobs/{jid}") as ws:
        msg = ws.receive_json()
        assert msg["status"] == "done"


def test_rename_persists_via_store(tmp_path):
    """rename 后通过 store 持久化，重启后能读回修改后的 speaker"""
    from transcribe_core.store import JobStore
    store = JobStore(str(tmp_path / "data"))
    reg = ModelRegistry(str(tmp_path / "config.json"),
                        is_downloaded_fn=lambda r: True, download_fn=lambda r: None)
    q = JobQueue(FakeBackend(), on_change=store.save, duration_fn=lambda p: 1.0,
                 extract_fn=lambda src, start, dur: src)
    c = TestClient(create_app(q, reg, store))
    jid = c.post("/jobs", json={"audio_path": "/x/a.m4a"}).json()["job_id"]
    _wait_done(c, jid)
    c.post(f"/jobs/{jid}/rename", json={"orig": "说话人A", "name": "李四"})
    # 重新加载存储中的 job，验证修改已持久化
    reloaded = {j.id: j for j in JobStore(str(tmp_path / "data")).load_all()}
    assert "李四" in reloaded[jid].transcript.to_txt()


def test_num_speakers_endpoint_updates_paused_job(tmp_path):
    from transcribe_core.jobs import Job
    reg = ModelRegistry(str(tmp_path / "config.json"),
                        is_downloaded_fn=lambda r: True, download_fn=lambda r: None)
    q = JobQueue(FakeBackend(), duration_fn=lambda p: 1.0,
                 extract_fn=lambda s, st, d: s)
    q.preload([Job(id="jp", audio_path="/x/a.m4a", status="paused", progress=0.4,
                   transcript=None, error=None, total_chunks=3, chunks_done=1)])
    c = TestClient(create_app(q, reg))
    assert c.post("/jobs/jp/num_speakers", json={"num_speakers": 3}).status_code == 200
    assert q.get("jp").num_speakers == 3


def test_num_speakers_endpoint_409_when_done(tmp_path):
    c = make_client(tmp_path)
    jid = c.post("/jobs", json={"audio_path": "/x/a.m4a"}).json()["job_id"]
    _wait_done(c, jid)
    assert c.post(f"/jobs/{jid}/num_speakers",
                  json={"num_speakers": 3}).status_code == 409


def test_get_job_returns_num_speakers(tmp_path):
    from transcribe_core.jobs import Job
    reg = ModelRegistry(str(tmp_path / "config.json"),
                        is_downloaded_fn=lambda r: True, download_fn=lambda r: None)
    q = JobQueue(FakeBackend())
    q.preload([Job(id="jg", audio_path="/x/a.m4a", status="paused", progress=0.4,
                   transcript=None, error=None, num_speakers=4)])
    c = TestClient(create_app(q, reg))
    assert c.get("/jobs/jg").json()["num_speakers"] == 4


def test_rediarize_endpoint(tmp_path):
    import time
    from transcribe_core.transcript import Transcript, Segment
    from transcribe_core.jobs import Job
    reg = ModelRegistry(str(tmp_path / "config.json"),
                        is_downloaded_fn=lambda r: True, download_fn=lambda r: None)
    q = JobQueue(FakeBackend(), duration_fn=lambda p: 1.0,
                 extract_fn=lambda s, st, d: s)
    q.preload([Job(id="jr", audio_path="/x/a.m4a", status="done", progress=1.0,
                   transcript=Transcript(segments=[Segment(0, 2, "你好", "说话人A")]),
                   error=None, total_chunks=1, chunks_done=1)])
    c = TestClient(create_app(q, reg))
    assert c.post("/jobs/jr/rediarize", json={"num_speakers": 2}).status_code == 200
    for _ in range(100):
        if q.get("jr").status == "done" and "jr" not in q._inflight and q.get("jr").progress == 1.0:
            break
        time.sleep(0.02)
    assert q.get("jr").status == "done"


def test_rediarize_endpoint_409_non_done(tmp_path):
    from transcribe_core.jobs import Job
    reg = ModelRegistry(str(tmp_path / "config.json"),
                        is_downloaded_fn=lambda r: True, download_fn=lambda r: None)
    q = JobQueue(FakeBackend())
    q.preload([Job(id="jn", audio_path="/x/a.m4a", status="paused", progress=0.4,
                   transcript=None, error=None)])
    c = TestClient(create_app(q, reg))
    assert c.post("/jobs/jn/rediarize", json={"num_speakers": 2}).status_code == 409
