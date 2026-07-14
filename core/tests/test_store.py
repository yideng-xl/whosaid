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
