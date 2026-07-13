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
    q = JobQueue(FakeBackend())
    jid = q.submit("/x/a.m4a")
    job = q.get(jid)
    assert job.status == "done"
    assert job.progress == 1.0
    assert [s.speaker for s in job.transcript.segments] == ["说话人A", "说话人B"]
    assert job.transcript.to_txt() == "说话人A：你好\n\n说话人B：在吗\n\n"


def test_run_job_failure_sets_error():
    q = JobQueue(BoomBackend())
    jid = q.submit("/x/a.m4a")
    job = q.get(jid)
    assert job.status == "failed"
    assert "模型未下载" in job.error


def test_progress_callback_called_monotonic():
    q = JobQueue(FakeBackend())
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

    q = JobQueue(SlowBackend())
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

    q = JobQueue(backend_factory=factory, registry=reg)

    q.submit("/x/a.m4a")
    assert received[-1][0] == "mlx-community/whisper-large-v3-mlx"

    reg.set_active("whisper-small")
    q.submit("/x/b.m4a")
    assert received[-1][0] == "mlx-community/whisper-small-mlx"
