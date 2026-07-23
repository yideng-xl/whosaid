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
        # 各 3s（>= interj 默认 2.5s）：两个人都不会被当作短插话折并，稳定切成两块
        return [(0.0, 3.0, "SPEAKER_00"), (3.0, 6.0, "SPEAKER_01")]


class BoomBackend(InferenceBackend):
    id = "boom"
    def transcribe(self, audio_path, language, initial_prompt):
        raise RuntimeError("模型未下载")
    def diarize(self, audio_path, num_speakers):
        # diarize-first：要让 transcribe 真正被调用到（从而抛出待测异常），
        # 需要给出非空轮次；空轮次会在进入 transcribe 前就被判为空音频。
        return [(0.0, 5.0, "SPEAKER_00")]


def test_run_job_success_produces_labeled_transcript():
    q = JobQueue(FakeBackend(), duration_fn=lambda p: 1.0,
                 extract_fn=lambda src, start, dur: src)
    jid = q.submit("/x/a.m4a")
    job = q.get(jid)
    assert job.status == "done"
    assert job.progress == 1.0
    # diarize-first：两块分别转写，relabel_blocks 把块的原始说话人标签按首次出现
    # 顺序归一化为 说话人A/B 后再贴到段上。
    assert [s.speaker for s in job.transcript.segments] == \
        ["说话人A", "说话人A", "说话人B", "说话人B"]
    assert job.transcript.to_txt() == "说话人A：你好在吗\n\n说话人B：你好在吗\n\n"


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
    """假后端记录每块的输入，验证发言块循环覆盖全时长且时间戳偏移正确。
    diarize-first：块边界由 diarize 输出（经 refine_turns 精炼）决定，不再是固定时长切块。"""
    from transcribe_core.jobs import JobQueue, Job
    from transcribe_core.transcript import Segment

    calls = []

    class ChunkBackend(InferenceBackend):
        id = "chunk"
        def transcribe(self, audio_path, language, initial_prompt):
            calls.append(audio_path)
            return [Segment(0.0, 1.0, "x")]  # 每块相对 0 的一段
        def diarize(self, audio_path, num_speakers):
            # 3 个发言块：A(0-120) B(120-240) A(240-250)，各段时长足够不被精炼合并
            return [(0.0, 120.0, "A"), (120.0, 240.0, "B"), (240.0, 250.0, "A")]

    # 注入假 extract：extract 返回块标记路径；duration_fn 在 diarize-first 下不再被 run_job 使用
    q = JobQueue(ChunkBackend(),
                 duration_fn=lambda p: 250.0,
                 extract_fn=lambda src, start, dur: f"/tmp/chunk_{start}.wav",
                 chunk_sec=120.0)
    jid = q.submit("/x/a.m4a")
    job = q.get(jid)
    assert job.status == "done"
    assert job.total_chunks == 3 and job.chunks_done == 3
    assert len(calls) == 3  # 转了 3 个发言块
    # 每块的片段被偏移到块起点：0,120,240
    starts = sorted(s.start for s in job.transcript.segments)
    assert starts == [0.0, 120.0, 240.0]


def test_pause_stops_at_chunk_boundary_and_persists():
    """diarize-first：块边界由 diarize 输出决定，此处构造 3 个交替说话人的发言块
    （各 120s，足够长不会被 refine_turns 精炼合并），验证暂停仍在块边界生效。"""
    from transcribe_core.jobs import JobQueue
    from transcribe_core.transcript import Segment
    import threading

    gate = threading.Event()   # 让第 1 块 transcribe 阻塞，好在块边界触发暂停

    class SlowChunk(InferenceBackend):
        id = "slow"
        def transcribe(self, audio_path, language, initial_prompt):
            gate.wait(timeout=2)   # 第一块等待，期间主线程 pause
            return [Segment(0.0, 1.0, "x")]
        def diarize(self, audio_path, num_speakers):
            return [(0.0, 120.0, "A"), (120.0, 240.0, "B"), (240.0, 360.0, "A")]

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
    """resume 免重跑分离：预置的 job 已带 blocks（上次分离结果），resume 应直接复用
    这份 blocks 继续逐块转写，而不重新调用 diarize（诊断 job.blocks is None 的分支判断）。"""
    from transcribe_core.jobs import JobQueue, Job
    from transcribe_core.transcript import Segment, Transcript

    calls = []
    diarize_calls = []

    class ChunkBackend(InferenceBackend):
        id = "chunk"
        def transcribe(self, audio_path, language, initial_prompt):
            calls.append(audio_path)
            return [Segment(0.0, 1.0, "x")]
        def diarize(self, audio_path, num_speakers):
            diarize_calls.append(audio_path)
            return [(0.0, 1000.0, "S0")]

    q = JobQueue(ChunkBackend(),
                 duration_fn=lambda p: 360.0,
                 extract_fn=lambda src, start, dur: f"/tmp/c_{start}.wav",
                 chunk_sec=120.0)
    # 预置一个已暂停、分离已完成(3 块)、转了 2 块的 job
    q.preload([Job(id="jr", audio_path="/x/a.m4a", status="paused", progress=0.72,
                   transcript=Transcript(segments=[Segment(0, 1, "a"), Segment(120, 121, "b")]),
                   error=None, total_chunks=3, chunks_done=2,
                   blocks=[(0.0, 120.0, "S0"), (120.0, 240.0, "S0"), (240.0, 360.0, "S0")])])
    assert q.resume("jr") is True
    import time
    for _ in range(50):
        if q.get("jr").status == "done":
            break
        time.sleep(0.02)
    job = q.get("jr")
    assert job.status == "done"
    assert len(calls) == 1          # 只补转了第 3 块，不重跑前 2 块
    assert diarize_calls == []      # blocks 已持久化，resume 不重跑分离
    assert job.chunks_done == 3


def test_pause_during_single_last_chunk_is_honored():
    """BUG-1 回归：单块(<2min)音频在(唯一/末)块转写途中点暂停，转完后应停在 paused 而非 done。
    循环无下一次迭代观察标志，须靠循环后补检查捕获。
    diarize-first 下 diarize 在 run_job 一开始就无条件同步跑完（不可暂停，是管线倒置的
    设计本身），因此本用例不再断言「暂停后不应进入 diarize」，只聚焦暂停是否被兑现。"""
    from transcribe_core.jobs import JobQueue
    from transcribe_core.transcript import Segment

    gate = threading.Event()

    class SlowSingle(InferenceBackend):
        id = "slowsingle"
        def transcribe(self, audio_path, language, initial_prompt):
            gate.wait(timeout=2)   # 唯一一块，阻塞期间主线程 pause
            return [Segment(0.0, 1.0, "x")]
        def diarize(self, audio_path, num_speakers):
            return [(0.0, 1000.0, "S0")]

    q = JobQueue(SlowSingle(), duration_fn=lambda p: 60.0,   # 单块
                 extract_fn=lambda src, start, dur: f"/tmp/s_{start}.wav",
                 chunk_sec=120.0)
    jid = q.submit_async("/x/a.m4a")
    time.sleep(0.2)                 # 进入(唯一块的)transcribe（此时 diarize 早已跑完，blocks 已定）
    assert q.pause(jid) is True     # 转写阶段可暂停
    gate.set()                      # 放该块过；转完后循环结束
    for _ in range(100):
        if q.get(jid).status in ("paused", "done", "failed"):
            break
        time.sleep(0.02)
    job = q.get(jid)
    assert job.status == "paused"   # 修复前会是 done：循环后补检查加回后应兑现暂停
    assert job.total_chunks == 1 and job.chunks_done == 1


def test_resume_rejects_second_call_while_inflight():
    """BUG-2 回归：已有线程在跑/排队(in-flight)时，二次 resume 幂等拒绝。"""
    from transcribe_core.jobs import JobQueue, Job
    from transcribe_core.transcript import Segment, Transcript
    q = JobQueue(FakeBackend(), duration_fn=lambda p: 1.0,
                 extract_fn=lambda src, start, dur: src)
    q.preload([Job(id="ji", audio_path="/x/a.m4a", status="paused", progress=0.4,
                   transcript=Transcript(segments=[Segment(0, 1, "a")]),
                   error=None, total_chunks=3, chunks_done=1)])
    q._inflight.add("ji")            # 模拟已有线程在跑/排队
    assert q.resume("ji") is False   # 幂等：拒绝二次续跑


def test_double_resume_under_gate_contention_runs_once():
    """BUG-2 端到端：闸门被 A 占住时对 failed 的 B 连点两次 resume，B 只应跑一遍
    （不重复 diarize、不丢改名）。"""
    from transcribe_core.jobs import JobQueue, Job

    gate = threading.Event()
    diarize_calls = []

    class GateBackend(InferenceBackend):
        id = "gate"
        def transcribe(self, audio_path, language, initial_prompt):
            gate.wait(timeout=2)     # 占住闸门的 A 任务在此阻塞
            return [Segment(0.0, 1.0, "x")]
        def diarize(self, audio_path, num_speakers):
            diarize_calls.append(audio_path)
            return [(0.0, 1000.0, "S0")]

    q = JobQueue(GateBackend(), duration_fn=lambda p: 60.0,
                 extract_fn=lambda src, start, dur: f"/tmp/g_{start}.wav",
                 chunk_sec=120.0)
    a = q.submit_async("/x/A.m4a")   # A 占住闸门（阻塞在 transcribe）
    time.sleep(0.2)
    q.preload([Job(id="B", audio_path="/x/B.m4a", status="failed", progress=0.0,
                   transcript=None, error="旧错", total_chunks=0, chunks_done=0)])
    r1 = q.resume("B")               # 此刻闸门被 A 占，两次都在 B 的 status 变 running 前
    r2 = q.resume("B")
    gate.set()                       # 放行 A，随后 B 排队跑
    for _ in range(200):
        if q.get("B").status in ("done", "failed") and q.get(a).status == "done":
            break
        time.sleep(0.02)
    assert r1 is True and r2 is False               # 第二次被幂等拒绝
    assert diarize_calls.count("/x/B.m4a") == 1     # B 只 diarize 一次


def test_empty_audio_fails_with_friendly_error():
    """BUG-3 回归：空/零时长音频不应抛 AttributeError，而是给人话错误。
    diarize-first 下"空音频"体现为 diarize 不出任何轮次（而非旧语义里 duration<=0），
    refine_turns([]) 得空块列表，循环零次迭代，transcript 仍为 None → 触发人话错误。"""
    from transcribe_core.jobs import JobQueue

    class EmptyBackend(InferenceBackend):
        id = "empty"
        def transcribe(self, audio_path, language, initial_prompt):
            return [Segment(0, 1, "不应被调用到")]
        def diarize(self, audio_path, num_speakers):
            return []

    q = JobQueue(EmptyBackend(), duration_fn=lambda p: 0.0,
                 extract_fn=lambda src, start, dur: src)
    jid = q.submit("/x/empty.m4a")   # 同步跑
    job = q.get(jid)
    assert job.status == "failed"
    assert "音频为空" in job.error


def test_resume_clears_stale_error():
    """BUG-4 回归：failed→resume→done 后，旧 error 应被清空。"""
    from transcribe_core.jobs import JobQueue, Job
    q = JobQueue(FakeBackend(), duration_fn=lambda p: 1.0,
                 extract_fn=lambda src, start, dur: src)
    q.preload([Job(id="je", audio_path="/x/a.m4a", status="failed", progress=0.0,
                   transcript=None, error="旧的失败信息", total_chunks=0, chunks_done=0)])
    assert q.resume("je") is True
    for _ in range(100):
        if q.get("je").status == "done":
            break
        time.sleep(0.02)
    job = q.get("je")
    assert job.status == "done"
    assert job.error is None         # 续跑成功后旧错误被清


def test_set_num_speakers_updates_paused_job():
    q = JobQueue(FakeBackend(), duration_fn=lambda p: 1.0,
                 extract_fn=lambda s, st, d: s)
    q.preload([Job(id="jp", audio_path="/x/a.m4a", status="paused", progress=0.4,
                   transcript=None, error=None, total_chunks=3, chunks_done=1)])
    assert q.set_num_speakers("jp", 3) is True
    assert q.get("jp").num_speakers == 3
    assert q.set_num_speakers("jp", None) is True   # 允许清回自动
    assert q.get("jp").num_speakers is None


def test_set_num_speakers_rejected_when_done():
    q = JobQueue(FakeBackend(), duration_fn=lambda p: 1.0,
                 extract_fn=lambda s, st, d: s)
    jid = q.submit("/x/a.m4a")
    assert q.get(jid).status == "done"
    assert q.set_num_speakers(jid, 3) is False       # 已完成走 rediarize


def test_set_num_speakers_rejected_after_blocks_set():
    """diarize-first 语义：分离已完成(blocks 已定)后锁定，不再依赖 progress≥0.85 魔法数字——
    这里即使 progress 不高，只要 blocks 已定（分离已跑完，逐块转写中）也应锁定。"""
    q = JobQueue(FakeBackend())
    q.preload([Job(id="jd", audio_path="/x/a.m4a", status="running", progress=0.3,
                   transcript=None, error=None, total_chunks=3, chunks_done=0,
                   blocks=[(0.0, 1.0, "A"), (1.0, 2.0, "B"), (2.0, 3.0, "A")])])
    assert q.set_num_speakers("jd", 3) is False


def test_set_num_speakers_allowed_while_running_before_blocks_set():
    """diarize-first 语义：running 但 blocks 尚未定(仍在分离中)时应仍可写入人数。"""
    q = JobQueue(FakeBackend())
    q.preload([Job(id="jd2", audio_path="/x/a.m4a", status="running", progress=0.05,
                   transcript=None, error=None)])
    assert q.set_num_speakers("jd2", 3) is True


def test_set_num_speakers_unknown_job():
    q = JobQueue(FakeBackend())
    assert q.set_num_speakers("nope", 3) is False


def test_rediarize_reruns_with_new_count_and_clears_renames():
    from transcribe_core.transcript import Transcript, Segment
    counts = []

    class CountBackend(InferenceBackend):
        id = "count"
        def transcribe(self, a, l, p):
            return [Segment(0, 2, "你好")]
        def diarize(self, a, num_speakers):
            counts.append(num_speakers)
            return [(0.0, 2.0, "SPEAKER_00")]

    q = JobQueue(CountBackend(), duration_fn=lambda p: 1.0,
                 extract_fn=lambda s, st, d: s)
    t = Transcript(segments=[Segment(0, 2, "你好", "说话人A")],
                   speaker_names={"说话人A": "张三"})
    q.preload([Job(id="jr", audio_path="/x/a.m4a", status="done", progress=1.0,
                   transcript=t, error=None, total_chunks=1, chunks_done=1)])
    assert q.rediarize("jr", 2) is True
    for _ in range(200):
        if counts and q.get("jr").status == "done" and "jr" not in q._inflight:
            break
        time.sleep(0.02)
    job = q.get("jr")
    assert job.status == "done" and job.progress == 1.0
    assert counts[-1] == 2                       # 用新人数
    assert job.transcript.speaker_names == {}    # 旧真名被清
    assert [s.speaker for s in job.transcript.segments] == ["说话人A"]  # 标签重新生成


def test_rediarize_rejects_non_done():
    q = JobQueue(FakeBackend())
    q.preload([Job(id="jn", audio_path="/x/a.m4a", status="paused", progress=0.4,
                   transcript=None, error=None)])
    assert q.rediarize("jn", 2) is False


def test_rediarize_rejects_when_inflight():
    from transcribe_core.transcript import Transcript, Segment
    q = JobQueue(FakeBackend(), duration_fn=lambda p: 1.0,
                 extract_fn=lambda s, st, d: s)
    q.preload([Job(id="ji", audio_path="/x/a.m4a", status="done", progress=1.0,
                   transcript=Transcript(segments=[Segment(0, 2, "你好", "说话人A")]),
                   error=None, total_chunks=1, chunks_done=1)])
    q._inflight.add("ji")
    assert q.rediarize("ji", 2) is False


def test_rediarize_flips_status_synchronously():
    """终审 Important-1 回归（新契约）:rediarize 返回 True 后应立即 running/0.0(不等
    worker)——全量重跑从头开始，进度归零而非停在旧的"仅分人阶段"0.85 标记；否则前端重订阅
    WS 会读到旧 done 帧、界面卡在旧结果。"""
    from transcribe_core.transcript import Transcript, Segment
    gate = threading.Event()

    class SlowDiarize(InferenceBackend):
        id = "slowd"
        def transcribe(self, a, l, p):
            return [Segment(0, 2, "你好")]
        def diarize(self, a, num_speakers):
            gate.wait(timeout=2)   # 阻住 worker,模拟闸门竞争/慢分人
            return [(0.0, 2.0, "S0")]

    q = JobQueue(SlowDiarize(), duration_fn=lambda p: 1.0,
                 extract_fn=lambda s, st, d: s)
    q.preload([Job(id="jf", audio_path="/x/a.m4a", status="done", progress=1.0,
                   transcript=Transcript(segments=[Segment(0, 2, "你好", "说话人A")]),
                   error=None, total_chunks=1, chunks_done=1)])
    assert q.rediarize("jf", 2) is True
    job = q.get("jf")
    assert job.status == "running" and job.progress == 0.0   # 同步已翻,不等 worker
    assert job.transcript is None and job.blocks is None     # 全量重跑：旧稿/旧块同步清空
    gate.set()
    for _ in range(100):
        if q.get("jf").status == "done" and "jf" not in q._inflight:
            break
        time.sleep(0.02)
    assert q.get("jf").status == "done"


def test_rediarize_failure_marks_failed_and_clears_old_transcript():
    """新契约:全量重跑语义下 rediarize 不再是"只重跑 diarize+align"的局部操作，而是
    复位后完整走一遍 run_job；旧 transcript 已在复位时清空，diarize 失败与普通任务失败
    走同一条异常处理路径——job 降级为 failed，旧稿不再可恢复（与 test_run_job_failure_
    sets_error 的失败契约一致）。这是本任务明确要求的行为变化，不是遗留 bug。"""
    from transcribe_core.transcript import Transcript, Segment

    class BoomDiarize(InferenceBackend):
        id = "boomd"
        def transcribe(self, a, l, p):
            return [Segment(0, 2, "你好")]
        def diarize(self, a, num_speakers):
            raise RuntimeError("分人炸了")

    q = JobQueue(BoomDiarize(), duration_fn=lambda p: 1.0,
                 extract_fn=lambda s, st, d: s)
    old = Transcript(segments=[Segment(0, 2, "你好", "说话人A")],
                     speaker_names={"说话人A": "张三"})
    q.preload([Job(id="jb", audio_path="/x/a.m4a", status="done", progress=1.0,
                   transcript=old, error=None, total_chunks=1, chunks_done=1)])
    assert q.rediarize("jb", 2) is True
    for _ in range(100):
        if "jb" not in q._inflight:
            break
        time.sleep(0.02)
    job = q.get("jb")
    assert job.status == "failed"                   # 全量重跑失败即普通失败，不再特殊恢复
    assert job.transcript is None                    # 旧稿已在复位阶段清空，未被保留
    assert job.error and "分人炸了" in job.error


class _FakeBackend:
    def __init__(self):
        self.transcribe_calls = []
    def transcribe(self, wav, lang, prompt):
        self.transcribe_calls.append(wav)
        return [Segment(0.0, 2.0, f"块{len(self.transcribe_calls)}")]
    def diarize(self, audio_path, num_speakers):
        # 两个人各说 20s → refine_turns 得两块
        return [(0.0, 20.0, "说话人A"), (20.0, 40.0, "说话人B")]


def test_run_job_diarize_first_assigns_block_speaker():
    be = _FakeBackend()
    q = JobQueue(backend=be, duration_fn=lambda p: 40.0,
                 extract_fn=lambda src, s, d: f"/fake/{s}.wav")
    jid = q.submit("/audio.m4a")
    job = q.get(jid)
    assert job.status == "done"
    # 两块 → 两次 transcribe，各块段贴对应说话人（说话人不被抹）
    assert len(be.transcribe_calls) == 2
    spks = {s.speaker for s in job.transcript.segments}
    assert spks == {"说话人A", "说话人B"}
    assert job.total_chunks == 2


def test_rediarize_full_rerun_with_new_count():
    """rediarize 新契约：全量重跑——改人数会重新走一遍 diarize-first 主流程，
    因此逐块 transcribe 会被再次调用（不再是"只重跑 diarize+align，不重听"）。"""
    be = _FakeBackend()
    q = JobQueue(backend=be, duration_fn=lambda p: 40.0,
                 extract_fn=lambda src, s, d: f"/fake/{s}.wav")
    jid = q.submit("/audio.m4a")
    before = len(be.transcribe_calls)
    ok = q.rediarize(jid, 3)   # 改人数 → 全量重跑，会再次逐块转写
    # rediarize 起后台线程，等它跑完
    import time as _t
    for _ in range(50):
        if q.get(jid).status == "done" and len(be.transcribe_calls) > before:
            break
        _t.sleep(0.05)
    assert ok is True
    assert len(be.transcribe_calls) > before  # 确实重转了
    assert q.get(jid).num_speakers == 3
