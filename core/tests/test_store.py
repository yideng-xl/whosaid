from transcribe_core.store import JobStore
from transcribe_core.jobs import Job
from transcribe_core.transcript import Segment, Transcript


def _done_job():
    t = Transcript(segments=[Segment(0, 1, "你好", "说话人A")])
    t.rename_speaker("说话人A", "张三")
    return Job(id="job1", audio_path="/x/a.m4a", status="done", progress=1.0,
               transcript=t, error=None)


def test_save_then_load_roundtrip(tmp_path):
    store = JobStore(str(tmp_path))
    store.save(_done_job())
    loaded = JobStore(str(tmp_path)).load_all()
    assert len(loaded) == 1
    j = loaded[0]
    assert j.id == "job1" and j.status == "done"
    assert j.transcript.to_txt() == "张三：你好\n\n"  # 改名也持久化


def test_idempotency_key_roundtrips_and_old_job_defaults_to_none(tmp_path):
    store = JobStore(str(tmp_path))
    job = _done_job()
    job.idempotency_key = "recording:req-1"
    store.save(job)
    loaded = JobStore(str(tmp_path)).load_all()[0]
    assert loaded.idempotency_key == "recording:req-1"

    import json
    (store.dir / "legacy.json").write_text(json.dumps({
        "id": "legacy", "audio_path": "/x/old.m4a", "status": "done",
        "progress": 1.0, "error": None, "transcript": None,
    }), encoding="utf-8")
    jobs = {item.id: item for item in JobStore(str(tmp_path)).load_all()}
    assert jobs["legacy"].idempotency_key is None


def test_transcription_prompt_roundtrips_and_old_job_defaults_to_none(tmp_path):
    store = JobStore(str(tmp_path))
    job = _done_job()
    job.transcription_prompt = "姓名：赵甲。"
    job.vocabulary_library_ids = ["names", "jiguan"]
    store.save(job)
    loaded = JobStore(str(tmp_path)).load_all()[0]
    assert loaded.transcription_prompt == "姓名：赵甲。"
    assert loaded.vocabulary_library_ids == ["names", "jiguan"]

    import json
    (store.dir / "legacy-prompt.json").write_text(json.dumps({
        "id": "legacy-prompt", "audio_path": "/x/old.m4a", "status": "done",
        "progress": 1.0, "error": None, "transcript": None,
    }), encoding="utf-8")
    jobs = {item.id: item for item in JobStore(str(tmp_path)).load_all()}
    assert jobs["legacy-prompt"].transcription_prompt is None
    assert jobs["legacy-prompt"].vocabulary_library_ids is None


def test_interrupted_started_job_releases_idempotency_key(tmp_path):
    """重启时非终态任务统一失败并释放键，允许用户重新启动实际 runner。"""
    store = JobStore(str(tmp_path))
    store.save(Job(
        id="ghost", audio_path="/missing/old.m4a", status="queued",
        progress=0.0, transcript=None, error=None,
        idempotency_key="recording:old-ghost",
    ))
    loaded = JobStore(str(tmp_path)).load_all()[0]
    assert loaded.status == "failed"
    assert loaded.idempotency_key is None


def test_load_all_returns_released_key_even_if_recovery_persist_fails(tmp_path, monkeypatch):
    store = JobStore(str(tmp_path))
    store.save(Job(
        id="ghost", audio_path="/missing/old.m4a", status="queued",
        progress=0.0, transcript=None, error=None,
        idempotency_key="recording:old-ghost",
    ))
    monkeypatch.setattr(store, "_persist_recovered_job", lambda job: (_ for _ in ()).throw(
        OSError("disk read-only")
    ))
    loaded = store.load_all()[0]
    assert loaded.status == "failed"
    assert loaded.idempotency_key is None


def test_running_job_marked_failed_on_load(tmp_path):
    store = JobStore(str(tmp_path))
    store.save(Job(id="job2", audio_path="/x/b.m4a", status="running",
                   progress=0.5, transcript=None, error=None))
    j = JobStore(str(tmp_path)).load_all()[0]
    assert j.status == "failed"
    assert "应用中断" in j.error


def test_load_all_empty_dir(tmp_path):
    assert JobStore(str(tmp_path)).load_all() == []


def test_paused_job_roundtrip_keeps_paused_and_chunks(tmp_path):
    store = JobStore(str(tmp_path))
    store.save(Job(id="jp", audio_path="/x/a.m4a", status="paused", progress=0.42,
                   transcript=Transcript(segments=[Segment(0, 1, "半句")]),
                   error=None, total_chunks=5, chunks_done=2))
    j = JobStore(str(tmp_path)).load_all()[0]
    assert j.status == "paused"  # 暂停跨重启保留
    assert j.total_chunks == 5 and j.chunks_done == 2
    assert j.transcript.plain_text() == "半句"


def test_store_roundtrips_blocks(tmp_path):
    """blocks 落盘+读回：resume 免重跑分离依赖此字段跨重启存活。
    JSON 会把 tuple 变 list，消费端（run_job）只做下标解包不依赖 tuple 身份，
    故断言按读回的实际类型（list）比较，load 端无需转回 tuple。"""
    store = JobStore(str(tmp_path))
    store.save(Job(id="jobX", audio_path="/a.m4a", status="done", progress=1.0,
                   transcript=None, error=None, blocks=[(0.0, 20.0, "说话人A")]))
    loaded = {j.id: j for j in JobStore(str(tmp_path)).load_all()}
    assert loaded["jobX"].blocks == [[0.0, 20.0, "说话人A"]]


def test_store_old_job_without_blocks_loads_as_none(tmp_path):
    import json
    store = JobStore(str(tmp_path))  # 建好 data_dir/jobs/ 目录
    (store.dir / "jobOld.json").write_text(json.dumps({
        "id": "jobOld", "audio_path": "/a.m4a", "status": "done",
        "progress": 1.0, "error": None, "transcript": None,
    }), encoding="utf-8")
    loaded = {j.id: j for j in JobStore(str(tmp_path)).load_all()}
    assert loaded["jobOld"].blocks is None
