import threading
import time

import pytest
from transcribe_core.jobs import JobQueue, Job
from transcribe_core.models import ModelRegistry
from transcribe_core.transcript import Segment
from transcribe_core.backend import InferenceBackend


class FakeBackend(InferenceBackend):
    id = "fake"
    def transcribe(self, audio_path, language, initial_prompt):
        return [Segment(0, 2, "你好"), Segment(2, 4, "在吗")]
    def diarize(self, audio_path, num_speakers):
        return [(0.0, 2.0, "SPEAKER_00"), (2.0, 4.0, "SPEAKER_01")]


class BoomBackend(InferenceBackend):
    id = "boom"
    def transcribe(self, audio_path, language, initial_prompt):
        raise RuntimeError("模型未下载")
    def diarize(self, audio_path, num_speakers):
        return []


def test_run_job_success_produces_labeled_transcript():
    q = JobQueue(FakeBackend(), duration_fn=lambda p: 1.0,
                 extract_fn=lambda src, start, dur: src)
    jid = q.submit("/x/a.m4a")
    job = q.get(jid)
    assert job.status == "done"
    assert job.progress == 1.0
    assert [s.speaker for s in job.transcript.segments] == ["说话人A", "说话人B"]
    assert job.transcript.to_txt() == "说话人A：你好\n\n说话人B：在吗\n\n"


def test_run_job_failure_sets_error():
    q = JobQueue(BoomBackend(), duration_fn=lambda p: 1.0,
                 extract_fn=lambda src, start, dur: src)
    jid = q.submit("/x/a.m4a")
    job = q.get(jid)
    assert job.status == "failed"
    assert "模型未下载" in job.error


def test_progress_callback_called_monotonic():
    q = JobQueue(FakeBackend(), duration_fn=lambda p: 1.0,
                 extract_fn=lambda src, start, dur: src)
    seen = []
    job = Job(id="j1", audio_path="/x/a.m4a", status="queued", progress=0.0,
              transcript=None, error=None)
    q._jobs["j1"] = job
    q.run_job(job, on_progress=lambda j: seen.append(j.progress))
    assert seen == sorted(seen)
    assert seen[-1] == 1.0


def test_single_concurrency():
    """三个任务并发提交，任意时刻推理段（transcribe/diarize）至多一个在跑。"""
    running = {"now": 0, "max": 0}
    lock = threading.Lock()

    class SlowBackend(InferenceBackend):
        id = "slow"
        def transcribe(self, audio_path, language, initial_prompt):
            with lock:
                running["now"] += 1
                running["max"] = max(running["max"], running["now"])
            time.sleep(0.1)
            with lock:
                running["now"] -= 1
            return [Segment(0, 1, "x")]
        def diarize(self, audio_path, num_speakers):
            return [(0.0, 1.0, "SPEAKER_00")]

    q = JobQueue(SlowBackend(), duration_fn=lambda p: 1.0,
                 extract_fn=lambda src, start, dur: src)
    ids = [q.submit_async("/x/a.m4a") for _ in range(3)]
    time.sleep(0.6)
    for jid in ids:
        assert q.get(jid).status == "done"
    assert running["max"] == 1  # 任意时刻至多一个在推理


def test_unsubscribe_removes_channel():
    q = JobQueue(FakeBackend())
    ch = q.subscribe("job1")
    assert q._subscribers["job1"] == [ch]
    q.unsubscribe("job1", ch)
    assert "job1" not in q._subscribers  # 列表清空后连键一并删除


def test_unsubscribe_keeps_other_channels():
    q = JobQueue(FakeBackend())
    ch1 = q.subscribe("job1")
    ch2 = q.subscribe("job1")
    q.unsubscribe("job1", ch1)
    assert q._subscribers["job1"] == [ch2]


def test_unsubscribe_unknown_job_id_is_noop():
    q = JobQueue(FakeBackend())
    q.unsubscribe("no-such-job", object())  # 不应抛异常


def test_switch_active_model_changes_backend_used_by_next_job(tmp_path):
    """核心回归：切换当前启用模型后，下一个任务应实际使用新模型的 repo 构造后端。"""
    reg = ModelRegistry(str(tmp_path / "config.json"),
                        is_downloaded_fn=lambda repo: True,
                        download_fn=lambda repo: None)
    received = []

    def factory(whisper_repo, diarize_repo):
        received.append((whisper_repo, diarize_repo))
        return FakeBackend()

    q = JobQueue(backend_factory=factory, registry=reg,
                 duration_fn=lambda p: 1.0, extract_fn=lambda src, start, dur: src)

    q.submit("/x/a.m4a")
    assert received[-1][0] == "mlx-community/whisper-large-v3-mlx"

    reg.set_active("whisper-small")
    q.submit("/x/b.m4a")
    assert received[-1][0] == "mlx-community/whisper-small-mlx"


def test_preload_and_no_id_collision():
    q = JobQueue(FakeBackend(), duration_fn=lambda p: 1.0,
                 extract_fn=lambda src, start, dur: src)
    # 预载一个历史 job（占用 job1 名）
    q.preload([Job(id="job1", audio_path="/x/a.m4a", status="done",
                   progress=1.0, transcript=None, error=None)])
    jid = q.submit("/x/b.m4a")  # 新任务不能覆盖已存在的 job1
    assert jid != "job1"
    assert q.get("job1") is not None and q.get(jid) is not None


def test_on_change_called_on_terminal_and_submit():
    seen = []
    q = JobQueue(FakeBackend(), on_change=lambda j: seen.append((j.id, j.status)),
                 duration_fn=lambda p: 1.0, extract_fn=lambda src, start, dur: src)
    jid = q.submit_async("/x/a.m4a")
    for _ in range(50):
        if q.get(jid).status in ("done", "failed"):
            break
        time.sleep(0.02)
    statuses = [s for _, s in seen]
    assert "queued" in statuses and "done" in statuses


def test_chunked_run_covers_full_duration_with_offsets():
    """假后端记录每块的输入，验证块循环覆盖全时长且时间戳偏移正确。"""
    from transcribe_core.jobs import JobQueue, Job
    from transcribe_core.transcript import Segment

    calls = []

    class ChunkBackend(InferenceBackend):
        id = "chunk"
        def transcribe(self, audio_path, language, initial_prompt):
            calls.append(audio_path)
            return [Segment(0.0, 1.0, "x")]  # 每块相对 0 的一段
        def diarize(self, audio_path, num_speakers):
            return [(0.0, 1000.0, "SPEAKER_00")]

    # 注入假 duration/extract：250 秒 → 3 块(120,120,10)；extract 返回块标记路径
    q = JobQueue(ChunkBackend(),
                 duration_fn=lambda p: 250.0,
                 extract_fn=lambda src, start, dur: f"/tmp/chunk_{start}.wav",
                 chunk_sec=120.0)
    jid = q.submit("/x/a.m4a")
    job = q.get(jid)
    assert job.status == "done"
    assert job.total_chunks == 3 and job.chunks_done == 3
    assert len(calls) == 3  # 转了 3 块
    # 第 i 块的片段被偏移 i*120：起点应是 0,120,240
    starts = sorted(s.start for s in job.transcript.segments)
    assert starts == [0.0, 120.0, 240.0]


def test_pause_stops_at_chunk_boundary_and_persists():
    from transcribe_core.jobs import JobQueue
    from transcribe_core.transcript import Segment
    import threading

    gate = threading.Event()   # 让第 1 块 transcribe 阻塞，好在块边界触发暂停
    resumed = []

    class SlowChunk(InferenceBackend):
        id = "slow"
        def transcribe(self, audio_path, language, initial_prompt):
            resumed.append(audio_path)
            gate.wait(timeout=2)   # 第一块等待，期间主线程 pause
            return [Segment(0.0, 1.0, "x")]
        def diarize(self, audio_path, num_speakers):
            return [(0.0, 1000.0, "S0")]

    q = JobQueue(SlowChunk(),
                 duration_fn=lambda p: 360.0,   # 3 块
                 extract_fn=lambda src, start, dur: f"/tmp/c_{start}.wav",
                 chunk_sec=120.0)
    jid = q.submit_async("/x/a.m4a")
    import time
    time.sleep(0.2)                 # 第 1 块进入 transcribe
    assert q.pause(jid) is True     # 转写阶段可暂停
    gate.set()                      # 放第 1 块过
    for _ in range(50):
        if q.get(jid).status == "paused":
            break
        time.sleep(0.02)
    job = q.get(jid)
    assert job.status == "paused"
    assert job.chunks_done == 1 and job.total_chunks == 3   # 停在块边界


def test_resume_continues_from_chunks_done():
    from transcribe_core.jobs import JobQueue, Job
    from transcribe_core.transcript import Segment, Transcript

    calls = []

    class ChunkBackend(InferenceBackend):
        id = "chunk"
        def transcribe(self, audio_path, language, initial_prompt):
            calls.append(audio_path)
            return [Segment(0.0, 1.0, "x")]
        def diarize(self, audio_path, num_speakers):
            return [(0.0, 1000.0, "S0")]

    q = JobQueue(ChunkBackend(),
                 duration_fn=lambda p: 360.0,
                 extract_fn=lambda src, start, dur: f"/tmp/c_{start}.wav",
                 chunk_sec=120.0)
    # 预置一个已暂停、转了 2 块的 job
    q.preload([Job(id="jr", audio_path="/x/a.m4a", status="paused", progress=0.56,
                   transcript=Transcript(segments=[Segment(0, 1, "a"), Segment(120, 121, "b")]),
                   error=None, total_chunks=3, chunks_done=2)])
    assert q.resume("jr") is True
    import time
    for _ in range(50):
        if q.get("jr").status == "done":
            break
        time.sleep(0.02)
    job = q.get("jr")
    assert job.status == "done"
    assert len(calls) == 1          # 只补转了第 3 块，不重跑前 2 块
    assert job.chunks_done == 3
