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
        # 各 3s（>= interj 默认 2.5s）：diarize-first 下两个人都不会被当作短插话折并
        return [(0.0, 3.0, "SPEAKER_00"), (3.0, 6.0, "SPEAKER_01")]


class FailingBackend(InferenceBackend):
    """转写失败的后端，模拟模型未下载等异常情况"""
    id = "failing"
    def transcribe(self, audio_path, language, initial_prompt):
        raise RuntimeError("模型未下载")
    def diarize(self, audio_path, num_speakers):
        # diarize-first：需给非空轮次才能让 transcribe 被调用到（从而暴露待测异常），
        # 空轮次会在进入 transcribe 前就被判为「音频为空」的另一条错误路径。
        return [(0.0, 5.0, "SPEAKER_00")]


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
    # diarize-first：relabel_blocks 把 diarize 原始标签按首次出现顺序归一化为 说话人A/B…
    assert "说话人A：你好" in job["txt"]


def test_get_job_returns_plain_txt_without_speaker_prefix(tmp_path):
    """done 任务的 plain_txt 是纯文字稿（各段 text 直接拼接），不含分人稿的"说话人X："前缀。"""
    c = make_client(tmp_path)
    jid = c.post("/jobs", json={"audio_path": "/x/a.m4a"}).json()["job_id"]
    resp = _wait_done(c, jid)
    assert resp["plain_txt"] != ""
    assert "：" not in resp["plain_txt"]


def test_rename_then_export(tmp_path):
    c = make_client(tmp_path)
    jid = c.post("/jobs", json={"audio_path": "/x/a.m4a"}).json()["job_id"]
    _wait_done(c, jid)
    c.post(f"/jobs/{jid}/rename", json={"orig": "说话人A", "name": "张三"})
    txt = c.get(f"/jobs/{jid}/export", params={"fmt": "txt"}).text
    assert txt.startswith("张三：你好")


def test_export_plain_returns_verbatim(tmp_path):
    """export?fmt=plain 返回逐字稿纯文本（不带人名前缀），以 [00: 时间戳开头。"""
    c = make_client(tmp_path)
    jid = c.post("/jobs", json={"audio_path": "/x/a.m4a"}).json()["job_id"]
    _wait_done(c, jid)
    r = c.get(f"/jobs/{jid}/export", params={"fmt": "plain"})
    assert r.status_code == 200
    assert r.text.startswith("[00:")  # 逐字稿以时间戳开头


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
    # diarize-first：relabel_blocks 把 diarize 原始标签归一化为 说话人A/B（即此处的"原始标签"）
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


def test_hf_settings_roundtrip(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    c = make_client(tmp_path)
    assert c.get("/settings/hf").json() == {"hf_token": None, "hf_endpoint": None}
    r = c.post("/settings/hf", json={"hf_token": "hf_abc123", "hf_endpoint": "https://hf-mirror.com"})
    assert r.json()["ok"] is True
    assert c.get("/settings/hf").json() == {"hf_token": "hf_abc123", "hf_endpoint": "https://hf-mirror.com"}
    import os
    assert os.environ.get("HF_TOKEN") == "hf_abc123"
    assert os.environ.get("HF_ENDPOINT") == "https://hf-mirror.com"


def test_hf_settings_clearing_removes_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "stale")
    c = make_client(tmp_path)
    c.post("/settings/hf", json={"hf_token": "", "hf_endpoint": None})
    import os
    assert os.environ.get("HF_TOKEN") is None


def test_download_endpoint_maps_401_to_readable_403(tmp_path):
    import httpx
    from huggingface_hub.errors import HfHubHTTPError

    def failing_download(repo):
        resp = httpx.Response(401, request=httpx.Request("GET", "https://huggingface.co/x"))
        raise HfHubHTTPError("401 Client Error", response=resp)

    reg = ModelRegistry(str(tmp_path / "config.json"),
                        is_downloaded_fn=lambda repo: False,
                        download_fn=failing_download)
    q = JobQueue(FakeBackend())
    c = TestClient(create_app(q, reg))
    resp = c.post("/models/whisper-small/download")
    assert resp.status_code == 403
    assert "令牌" in resp.json()["detail"]
    assert "yideng-xl.github.io/whosaid" in resp.json()["detail"]


def test_delete_model_endpoint_calls_delete_fn_and_clears_downloaded(tmp_path):
    """DELETE /models/{id}：应调用 registry.delete → delete_fn 收到 repo，且状态里
    downloaded 标记随之清除（GET /models 能看到 downloaded: False）。"""
    calls = []
    reg = ModelRegistry(str(tmp_path / "config.json"),
                        is_downloaded_fn=lambda repo: False,
                        download_fn=lambda repo: None,
                        delete_fn=lambda repo: calls.append(repo))
    reg.download("whisper-small")
    q = JobQueue(FakeBackend())
    c = TestClient(create_app(q, reg))
    resp = c.delete("/models/whisper-small")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert calls == ["mlx-community/whisper-small-mlx"]
    models = {m["id"]: m for m in c.get("/models").json()}
    assert models["whisper-small"]["downloaded"] is False


def test_delete_model_endpoint_without_delete_fn_raises(tmp_path):
    """registry 未注入 delete_fn 时应报错（服务端未妥善配置），而不是假装成功。
    TestClient 默认会把服务端未捕获异常原样抛出（raise_server_exceptions=True），
    故这里直接断言异常类型，而非某个 HTTP 状态码。"""
    import pytest
    reg = ModelRegistry(str(tmp_path / "config.json"),
                        is_downloaded_fn=lambda repo: False,
                        download_fn=lambda repo: None)
    q = JobQueue(FakeBackend())
    c = TestClient(create_app(q, reg))
    with pytest.raises(RuntimeError):
        c.delete("/models/whisper-small")


def test_model_progress_zero_when_no_cache_dir(tmp_path, monkeypatch):
    """未开始下载（缓存目录不存在）：downloaded_bytes/percent 均为 0，total_bytes 按 size_mb 估算。"""
    monkeypatch.setattr("huggingface_hub.constants.HF_HUB_CACHE", str(tmp_path / "hf_cache"))
    c = make_client(tmp_path)
    resp = c.get("/models/whisper-small/progress")
    assert resp.status_code == 200
    d = resp.json()
    assert d == {"downloaded_bytes": 0, "total_bytes": 484 * 1024 * 1024, "percent": 0.0}


def test_model_progress_reflects_partial_cache_dir_size(tmp_path, monkeypatch):
    """下载中：按 HF 缓存目录（models--{org}--{name}）里已落地文件的总大小算 downloaded_bytes，
    percent = downloaded/total*100（未封顶场景）。"""
    cache_root = tmp_path / "hf_cache"
    monkeypatch.setattr("huggingface_hub.constants.HF_HUB_CACHE", str(cache_root))
    model_dir = cache_root / "models--mlx-community--whisper-small-mlx" / "blobs"
    model_dir.mkdir(parents=True)
    (model_dir / "weights.bin").write_bytes(b"x" * (1024 * 1024))  # 1MB
    c = make_client(tmp_path)
    d = c.get("/models/whisper-small/progress").json()
    assert d["downloaded_bytes"] == 1024 * 1024
    assert d["total_bytes"] == 484 * 1024 * 1024
    assert 0 < d["percent"] < 99


def test_model_progress_not_doubled_by_snapshot_symlinks(tmp_path, monkeypatch):
    """真实 HF 缓存布局：blobs/ 下是真实数据文件，snapshots/<rev>/ 下是指向 blobs 的软链
    （文件名与 blobs 里不同，模拟 HF 用 hash 命名 blob、快照里用可读文件名软链指向它）。
    旧实现用 rglob("*") 遍历整个模型目录会把 blobs 里的真文件与 snapshots 里的软链各计一次，
    downloaded_bytes 变成约两倍；应只按 blobs/ 的真实字节数计，不被 snapshots 的软链翻倍。"""
    cache_root = tmp_path / "hf_cache"
    monkeypatch.setattr("huggingface_hub.constants.HF_HUB_CACHE", str(cache_root))
    model_root = cache_root / "models--mlx-community--whisper-small-mlx"
    blobs_dir = model_root / "blobs"
    blobs_dir.mkdir(parents=True)
    blob_file = blobs_dir / "abcd1234"
    blob_file.write_bytes(b"x" * (2 * 1024 * 1024))  # 2MB 真实数据

    snapshot_dir = model_root / "snapshots" / "rev1"
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / "model.bin").symlink_to(blob_file)  # 软链指向 blob，不应重复计入

    c = make_client(tmp_path)
    d = c.get("/models/whisper-small/progress").json()
    assert d["downloaded_bytes"] == 2 * 1024 * 1024  # 只按 blobs 算，不被 snapshots 软链翻倍
    total = 484 * 1024 * 1024
    assert d["percent"] == (2 * 1024 * 1024) / total * 100


def test_model_progress_caps_at_99_percent(tmp_path, monkeypatch):
    """size_mb 是估算值，缓存目录实际大小超过/等于估算总量时，percent 封顶 99，避免看起来"下完了"
    但下载请求其实还没返回。"""
    cache_root = tmp_path / "hf_cache"
    monkeypatch.setattr("huggingface_hub.constants.HF_HUB_CACHE", str(cache_root))
    model_dir = cache_root / "models--pyannote--speaker-diarization-community-1" / "blobs"
    model_dir.mkdir(parents=True)
    (model_dir / "big.bin").write_bytes(b"x" * (200 * 1024 * 1024))  # 200MB >> size_mb(90)
    c = make_client(tmp_path)
    d = c.get("/models/pyannote-community-1/progress").json()
    assert d["percent"] == 99.0


def test_model_progress_404_for_unknown_model(tmp_path):
    c = make_client(tmp_path)
    resp = c.get("/models/does-not-exist/progress")
    assert resp.status_code == 404


def test_download_endpoint_maps_other_http_error_to_502(tmp_path):
    import httpx
    from huggingface_hub.errors import HfHubHTTPError

    def failing_download(repo):
        resp = httpx.Response(500, request=httpx.Request("GET", "https://huggingface.co/x"))
        raise HfHubHTTPError("500 Server Error", response=resp)

    reg = ModelRegistry(str(tmp_path / "config.json"),
                        is_downloaded_fn=lambda repo: False,
                        download_fn=failing_download)
    q = JobQueue(FakeBackend())
    c = TestClient(create_app(q, reg))
    resp = c.post("/models/whisper-small/download")
    assert resp.status_code == 502
