# 人数改为每条记录属性 + 只重跑「拆分人声」 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「预计人数」从侧栏全局输入改为每条录音记录自己的属性(右侧面板顶部),并支持已完成任务在不重听的前提下用新人数只重跑「拆分人声」(diarize+align)。

**Architecture:** 后端在 `JobQueue` 增 `set_num_speakers`(分人前写入人数)与 `rediarize`(复用单并发闸门,拿已存文字段重跑 diarize+align、生成全新说话人标签、丢弃旧真名),server 暴露两个 POST 接口 + `GET /jobs/{id}` 回显 `num_speakers`。前端删侧栏全局框,人数控件随记录放右侧面板,done 且值变动时出现「重新分人」按钮,改过真名先弹确认。

**Tech Stack:** Python 3.13 + FastAPI + pytest(core);Tauri 2 + SvelteKit + Svelte 5 runes + TypeScript + vitest(desktop)。

## Global Constraints

- 所有代码注释、UI 文案用**中文**。
- **后端 pytest 必须在 `core/` 目录下执行**(否则 pyproject 的 filterwarnings/默认 `-m "not slow"` 不生效)。python 用 `core/venv/bin/python`。
- **前端 fetch 必须用 `127.0.0.1`**(现状已如此,勿改成 localhost)。
- diarize 读取人数处已是 `job.num_speakers or self.num_speakers`(jobs.py:216),写入 `job.num_speakers` 即天然生效,**不改读取逻辑**。
- 重新分人复用现有 `_infer_gate`(单并发)与 `_inflight`(幂等守卫),不新增并发原语。
- 真名存于 `Transcript.speaker_names`;`job.transcript = Transcript(segments=labeled)` 会自带空 `speaker_names`,真名随之清空——**这是预期行为,不要迁移旧名**。
- 提交信息用祈使中文短句(如 `feat: 新增 set_num_speakers`)。

---

### Task 1: 后端 `JobQueue.set_num_speakers`

分人前写入人数;done 与"正在分人(running & progress≥0.85)"拒绝。

**Files:**
- Modify: `core/transcribe_core/jobs.py`(在 `resume` 方法后新增)
- Test: `core/tests/test_jobs.py`(文件末尾追加)

**Interfaces:**
- Produces: `JobQueue.set_num_speakers(job_id: str, n: int | None) -> bool`

- [ ] **Step 1: 写失败测试**(追加到 `core/tests/test_jobs.py` 末尾)

```python
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


def test_set_num_speakers_rejected_while_diarizing():
    q = JobQueue(FakeBackend())
    q.preload([Job(id="jd", audio_path="/x/a.m4a", status="running", progress=0.9,
                   transcript=None, error=None)])
    assert q.set_num_speakers("jd", 3) is False       # 正在分人锁定


def test_set_num_speakers_unknown_job():
    q = JobQueue(FakeBackend())
    assert q.set_num_speakers("nope", 3) is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd core && venv/bin/python -m pytest tests/test_jobs.py -k set_num_speakers -v`
Expected: FAIL(`AttributeError: 'JobQueue' object has no attribute 'set_num_speakers'`)

- [ ] **Step 3: 实现**(在 `core/transcribe_core/jobs.py` 的 `resume` 方法之后、`get` 之前插入)

```python
    def set_num_speakers(self, job_id: str, n: int | None) -> bool:
        """分人前写入预计人数(供 diarize 约束)。仅在尚未分人时允许:
        done 走 rediarize;正在分人(running & progress≥0.85)锁定;其余(queued/paused/
        failed/running<0.85)可写。返回是否被接受。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status == "done":
                return False
            if job.status == "running" and job.progress >= 0.85:
                return False
            job.num_speakers = n
        self._notify(job)   # 触发 store.save 持久化
        return True
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd core && venv/bin/python -m pytest tests/test_jobs.py -k set_num_speakers -v`
Expected: PASS(4 passed)

- [ ] **Step 5: 提交**

```bash
git add core/transcribe_core/jobs.py core/tests/test_jobs.py
git commit -m "feat: JobQueue.set_num_speakers 分人前写入人数"
```

---

### Task 2: 后端 `JobQueue.rediarize` + `_run_rediarize`

已完成任务用新人数只重跑 diarize+align;复用闸门与幂等守卫;真名清空。

**Files:**
- Modify: `core/transcribe_core/jobs.py`(在 `set_num_speakers` 后新增两方法)
- Test: `core/tests/test_jobs.py`(追加)

**Interfaces:**
- Consumes: `align`、`Transcript`(jobs.py 顶部已 import);`_infer_gate`、`_inflight`、`_emit`、`_notify`、`_backend_factory`/`_registry`
- Produces: `JobQueue.rediarize(job_id: str, n: int | None) -> bool`

- [ ] **Step 1: 写失败测试**(追加到 `core/tests/test_jobs.py` 末尾)

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd core && venv/bin/python -m pytest tests/test_jobs.py -k rediarize -v`
Expected: FAIL(`AttributeError: ... 'rediarize'`)

- [ ] **Step 3: 实现**(在 `set_num_speakers` 之后插入)

```python
    def rediarize(self, job_id: str, n: int | None) -> bool:
        """已完成任务用新人数只重跑「拆分人声」(diarize+align),不重听。
        要求 status==done 且有文字稿;in-flight 则幂等拒绝。返回是否被接受。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != "done" or job.transcript is None:
                return False
            if job_id in self._inflight:
                return False
            self._inflight.add(job_id)
            job.num_speakers = n
        threading.Thread(target=self._run_rediarize, args=(job, self._emit),
                         daemon=True).start()
        return True

    def _run_rediarize(self, job: Job, on_progress: Callable[[Job], None]) -> None:
        """重跑分人:拿已存文字段 → diarize(新人数) → align(生成全新 说话人A/B,
        旧真名随旧 transcript 丢弃)。走单并发闸门,与普通任务串行。"""
        try:
            with _infer_gate:
                try:
                    backend = self.backend
                    if self._backend_factory is not None and self._registry is not None:
                        backend = self._backend_factory(
                            self._registry.active_repo("transcribe"),
                            self._registry.active_repo("diarize"),
                        )
                    job.status = "running"
                    job.error = None
                    job.progress = 0.85           # 进入分人阶段(phase=diarizing)
                    on_progress(job); self._notify(job)
                    segments = job.transcript.segments
                    turns = backend.diarize(job.audio_path,
                                            job.num_speakers or self.num_speakers)
                    job.progress = 0.95
                    on_progress(job)
                    labeled = align(segments, turns)
                    job.transcript = Transcript(segments=labeled)  # 新稿,真名清空
                    job.progress = 1.0
                    job.status = "done"
                    on_progress(job); self._notify(job)
                except Exception as e:
                    job.status = "failed"
                    job.error = str(e)
                    on_progress(job); self._notify(job)
        finally:
            with self._lock:
                self._inflight.discard(job.id)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd core && venv/bin/python -m pytest tests/test_jobs.py -k rediarize -v`
Expected: PASS(3 passed)

- [ ] **Step 5: 全量后端测试防回归**

Run: `cd core && venv/bin/python -m pytest -q`
Expected: 全绿(原有 + 新增)

- [ ] **Step 6: 提交**

```bash
git add core/transcribe_core/jobs.py core/tests/test_jobs.py
git commit -m "feat: JobQueue.rediarize 只重跑拆分人声"
```

---

### Task 3: server 接口(num_speakers / rediarize / GET 回显)

**Files:**
- Modify: `core/transcribe_core/server.py`
- Test: `core/tests/test_server.py`(追加)

**Interfaces:**
- Consumes: `queue.set_num_speakers`、`queue.rediarize`(Task 1/2)
- Produces: `POST /jobs/{id}/num_speakers`、`POST /jobs/{id}/rediarize`(body `{num_speakers: int|null}`);`GET /jobs/{id}` 响应新增 `num_speakers`

- [ ] **Step 1: 写失败测试**(追加到 `core/tests/test_server.py` 末尾)

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd core && venv/bin/python -m pytest tests/test_server.py -k "num_speakers or rediarize" -v`
Expected: FAIL(404/405:接口未定义;或 KeyError num_speakers)

- [ ] **Step 3: 实现**

3a. 在 `server.py` 的 `RenameReq` 后新增请求模型:

```python
class NumSpeakersReq(BaseModel):
    num_speakers: int | None = None  # 预计说话人数;None=自动
```

3b. `get_job` 的返回 dict 增加 `num_speakers`(在 `"phase": phase, ...` 同一 return 里追加一行):

```python
        return {
            "id": j.id, "status": j.status, "progress": j.progress, "error": j.error,
            "total_chunks": j.total_chunks, "chunks_done": j.chunks_done,
            "phase": phase, "txt": txt, "speakers": speakers,
            "num_speakers": j.num_speakers,
        }
```

3c. 在 `rename` 接口之后新增两个接口:

```python
    @app.post("/jobs/{job_id}/num_speakers")
    def set_num_speakers(job_id: str, req: NumSpeakersReq):
        _job_or_404(job_id)
        if not queue.set_num_speakers(job_id, req.num_speakers):
            raise HTTPException(409, "当前不可修改人数(已完成请用重新分人,或正在分人中)")
        return {"ok": True}

    @app.post("/jobs/{job_id}/rediarize")
    def rediarize(job_id: str, req: NumSpeakersReq):
        _job_or_404(job_id)
        if not queue.rediarize(job_id, req.num_speakers):
            raise HTTPException(409, "仅已完成的任务可重新分人")
        return {"ok": True}
```

- [ ] **Step 4: 跑测试确认通过 + 全量 server 测试**

Run: `cd core && venv/bin/python -m pytest tests/test_server.py -q`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add core/transcribe_core/server.py core/tests/test_server.py
git commit -m "feat: num_speakers/rediarize 接口 + GET 回显人数"
```

---

### Task 4: 前端 api 客户端 + 纯逻辑辅助

**Files:**
- Modify: `desktop/src/lib/api.ts`
- Create: `desktop/src/lib/jobState.ts`
- Test: `desktop/src/lib/api.test.ts`(追加)、`desktop/src/lib/jobState.test.ts`(新建)

**Interfaces:**
- Produces:
  - `api.setNumSpeakers(id: string, n: number | null): Promise<void>`
  - `api.rediarize(id: string, n: number | null): Promise<void>`
  - `JobDetail.num_speakers: number | null`
  - `canEditSpeakerCount(status: string, progress: number): boolean`
  - `isRenamed(speakers: Speaker[]): boolean`
  - `parseCount(v: string): number | null`

- [ ] **Step 1: 写失败测试**

1a. 追加到 `desktop/src/lib/api.test.ts` 的 `describe("api", ...)` 内:

```typescript
  it("setNumSpeakers POSTs num_speakers", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal("fetch", fetchMock);
    const api = createApi(2222);
    await api.setNumSpeakers("job7", 3);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:2222/jobs/job7/num_speakers",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ num_speakers: 3 }) }),
    );
  });

  it("rediarize POSTs num_speakers (null 允许)", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal("fetch", fetchMock);
    const api = createApi(2222);
    await api.rediarize("job7", null);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:2222/jobs/job7/rediarize",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ num_speakers: null }) }),
    );
  });
```

1b. 新建 `desktop/src/lib/jobState.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { canEditSpeakerCount, isRenamed, parseCount } from "./jobState";

describe("jobState", () => {
  it("canEditSpeakerCount 正在分人时锁定", () => {
    expect(canEditSpeakerCount("running", 0.9)).toBe(false);   // diarizing
    expect(canEditSpeakerCount("running", 0.4)).toBe(true);    // 听写中
    expect(canEditSpeakerCount("queued", 0)).toBe(true);
    expect(canEditSpeakerCount("paused", 0.5)).toBe(true);
    expect(canEditSpeakerCount("failed", 0)).toBe(true);
    expect(canEditSpeakerCount("done", 1)).toBe(true);
  });

  it("isRenamed 检测是否改过真名", () => {
    expect(isRenamed([{ orig: "说话人A", name: "说话人A" }])).toBe(false);
    expect(isRenamed([{ orig: "说话人A", name: "张三" }])).toBe(true);
    expect(isRenamed([])).toBe(false);
  });

  it("parseCount 规整输入", () => {
    expect(parseCount("3")).toBe(3);
    expect(parseCount("")).toBe(null);
    expect(parseCount("0")).toBe(null);
    expect(parseCount("-2")).toBe(null);
    expect(parseCount("abc")).toBe(null);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd desktop && npm test -- --run`
Expected: FAIL(`setNumSpeakers is not a function` / 无法解析 `./jobState`)

- [ ] **Step 3: 实现**

3a. `desktop/src/lib/api.ts`:`JobDetail` 接口增加一行 `num_speakers: number | null;`。在 `resumeJob` 之后、`deleteJob` 之前(或任意方法区)新增:

```typescript
    async setNumSpeakers(id: string, n: number | null): Promise<void> {
      await j(await fetch(`${base}/jobs/${id}/num_speakers`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ num_speakers: n }),
      }));
    },

    async rediarize(id: string, n: number | null): Promise<void> {
      await j(await fetch(`${base}/jobs/${id}/rediarize`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ num_speakers: n }),
      }));
    },
```

3b. 新建 `desktop/src/lib/jobState.ts`:

```typescript
// 任务状态相关的纯逻辑(可单测,供 TranscriptView 复用)
import type { Speaker } from "./api";

// 人数框是否可编辑:正在分人(running & progress≥0.85)锁定,其余状态可编辑
export function canEditSpeakerCount(status: string, progress: number): boolean {
  if (status === "running" && progress >= 0.85) return false;
  return ["queued", "running", "paused", "failed", "done"].includes(status);
}

// 是否改过真名(决定重新分人前是否弹确认)
export function isRenamed(speakers: Speaker[]): boolean {
  return speakers.some((s) => s.name !== s.orig);
}

// 输入框字符串 → 人数值:空/非正整数一律 null(自动)
export function parseCount(v: string): number | null {
  const n = parseInt(v, 10);
  return Number.isFinite(n) && n > 0 ? n : null;
}
```

- [ ] **Step 4: 跑测试确认通过 + 类型检查**

Run: `cd desktop && npm test -- --run && npm run check`
Expected: 测试全绿;svelte-check 0 errors

- [ ] **Step 5: 提交**

```bash
git add desktop/src/lib/api.ts desktop/src/lib/jobState.ts desktop/src/lib/api.test.ts desktop/src/lib/jobState.test.ts
git commit -m "feat: 前端 setNumSpeakers/rediarize 接口 + jobState 纯逻辑"
```

---

### Task 5: 侧栏移除全局人数框 + 「分人中」徽标

**Files:**
- Modify: `desktop/src/lib/Sidebar.svelte`

**Interfaces:**
- Consumes: `JobSummary`(含 `status`、`progress`)
- 移除: `expectedSpeakers` prop(下游 Task 7 的 +page 同步移除绑定)

- [ ] **Step 1: 先读文件**

Read `desktop/src/lib/Sidebar.svelte` 全文(当前含 `expectedSpeakers = $bindable("")` prop、`.spk-hint` 输入块、`statusOf` 徽标)。

- [ ] **Step 2: 移除全局人数框**

- 删除 `$props()` 解构里的 `expectedSpeakers = $bindable(""),` 及类型 `expectedSpeakers?: string;`。
- 删除模板中整个 `<label class="spk-hint" ...>...</label>` 块。
- 删除 `<style>` 里 `.spk-hint`、`.spk-hint input`、`.spk-tip` 三条规则,以及深色模式里 `--input-bg`(若仅此处用)。

- [ ] **Step 3: 「分人中」徽标**

在 `statusOf` 函数后新增按进度细分的辅助,并在模板改用它:

```svelte
  function badgeFor(job: JobSummary) {
    // 分人阶段(running 且进度进入 0.85+)显示「分人中」,避免与听写混淆
    if (job.status === "running" && job.progress >= 0.85)
      return { label: "分人中", cls: "running" };
    return statusOf(job.status);
  }
```

模板里把 `{statusOf(job.status).cls}` / `{statusOf(job.status).label}` 两处改为 `{badgeFor(job).cls}` / `{badgeFor(job).label}`。

- [ ] **Step 4: 类型检查**

Run: `cd desktop && npm run check`
Expected: 0 errors(会顺带暴露 +page 仍在 `bind:expectedSpeakers` → Task 7 修复;若本步报此错,记录待 Task 7 解决,不在此改 +page)
注:若 svelte-check 因 +page 绑定报错,先跳过 commit,与 Task 7 合并验证;否则本步可独立通过。

- [ ] **Step 5: 提交**

```bash
git add desktop/src/lib/Sidebar.svelte
git commit -m "feat: 侧栏移除全局人数框 + 新增分人中徽标"
```

---

### Task 6: 详情面板人数控件 + 「重新分人」+ 确认弹窗

**Files:**
- Modify: `desktop/src/lib/TranscriptView.svelte`

**Interfaces:**
- Consumes: `api`(含 `setNumSpeakers`、`getJob().num_speakers`)、`canEditSpeakerCount`/`isRenamed`/`parseCount`(Task 4)、`onRediarize`(新 prop,由 Task 7 的 +page 传入)
- Produces: 新 prop `onRediarize: (n: number | null) => void | Promise<void>`

- [ ] **Step 1: 先读文件**

Read `desktop/src/lib/TranscriptView.svelte` 全文,弄清:`$props()` 里现有 `api/jobId/audioPath/status/onPause/onResume`;内部 `detail`(getJob 结果,现有 1s 轮询直到终态)、`transitioning` 暂停态;导出按钮与 SpeakerRename 区。

- [ ] **Step 2: 引入依赖与状态**

- import:`import { canEditSpeakerCount, isRenamed, parseCount } from "./jobState";`
- `$props()` 增加 `onRediarize`:
  ```svelte
  onRediarize = () => {},
  ```
  类型:`onRediarize?: (n: number | null) => void | Promise<void>;`
- 新增本地 state:
  ```svelte
  let countDraft = $state("");          // 人数输入框(字符串,""=自动)
  let countSyncedFor = $state("");      // 已用哪个 job 的回显同步过草稿,防重复覆盖用户输入
  let showRediarizeConfirm = $state(false);
  let countSaveTimer: ReturnType<typeof setTimeout> | null = null;
  ```

- [ ] **Step 3: 草稿与回显同步**

新增 `$effect`:当切换到新 job、或该 job 首次拿到 `detail.num_speakers` 时,用回显值初始化草稿(此后用户输入不再被覆盖):

```svelte
  $effect(() => {
    if (detail && countSyncedFor !== jobId) {
      countDraft = detail.num_speakers != null ? String(detail.num_speakers) : "";
      countSyncedFor = jobId;
    }
  });
```

- [ ] **Step 4: 派生:能否编辑、是否显示「重新分人」**

```svelte
  const editable = $derived(
    !!detail && canEditSpeakerCount(detail.status, detail.progress)
  );
  // done 且草稿人数 ≠ 上次分人所用值 → 出现「重新分人」
  const baselineCount = $derived(detail?.num_speakers != null ? String(detail.num_speakers) : "");
  const showRediarize = $derived(!!detail && detail.status === "done" && countDraft !== baselineCount);
```

- [ ] **Step 5: 输入处理**

- 非 done 可编辑状态:输入防抖写后端;done:仅暂存草稿(点按钮才提交)。

```svelte
  function onCountInput() {
    if (!detail || detail.status === "done") return;   // done 只暂存,等「重新分人」
    if (countSaveTimer) clearTimeout(countSaveTimer);
    const n = parseCount(countDraft);
    countSaveTimer = setTimeout(() => { void api.setNumSpeakers(jobId, n); }, 600);
  }

  function clickRediarize() {
    if (detail && isRenamed(detail.speakers)) showRediarizeConfirm = true;
    else void doRediarize();
  }

  async function doRediarize() {
    showRediarizeConfirm = false;
    countSyncedFor = "";              // 允许下轮用新回显重同步草稿
    await onRediarize(parseCount(countDraft));
  }
```

- [ ] **Step 6: 控件与弹窗标记**

在面板顶部(文件名/状态一带之下、文字稿之上)加人数区:

```svelte
  <div class="count-row">
    <label>
      人数
      <input type="number" min="1" max="20" placeholder="自动"
             bind:value={countDraft} oninput={onCountInput} disabled={!editable} />
    </label>
    {#if detail && detail.status === "running" && detail.progress >= 0.85}
      <span class="count-hint">重新分人中…</span>
    {:else if showRediarize}
      <button class="rediarize" onclick={clickRediarize}>重新分人</button>
    {/if}
  </div>

  {#if showRediarizeConfirm}
    <div class="modal-backdrop" role="presentation">
      <div class="modal" role="dialog" aria-modal="true">
        <div class="modal-title">重新分人</div>
        <p class="modal-body">
          重新分人会重新划分说话人,<b>已改的真名会被清空、需重新认领</b>。文字稿不受影响。确定继续?
        </p>
        <div class="modal-actions">
          <button class="btn-cancel" onclick={() => (showRediarizeConfirm = false)}>取消</button>
          <button class="btn-danger" onclick={doRediarize}>确定重新分人</button>
        </div>
      </div>
    </div>
  {/if}
```

样式:复用与 `+page.svelte` 一致的 `.modal-backdrop/.modal/.modal-title/.modal-body/.modal-actions/.btn-cancel/.btn-danger`(Svelte 样式局部,需在本组件 `<style>` 内补一份;可从 +page 拷贝),另加 `.count-row`(flex、gap)、`.count-row input`(宽 54px)、`.count-hint`(muted)、`.rediarize`(小主色按钮)。深浅色都要覆盖(参照现有面板配色变量)。

- [ ] **Step 7: 类型检查 + 单测**

Run: `cd desktop && npm run check && npm test -- --run`
Expected: svelte-check 0 errors(前提:Task 7 已提供 onRediarize;若单独跑本任务,+page 尚未传 onRediarize 但有默认值 `() => {}`,不影响类型);vitest 全绿。

- [ ] **Step 8: 提交**

```bash
git add desktop/src/lib/TranscriptView.svelte
git commit -m "feat: 详情面板人数控件 + 重新分人 + 改名确认"
```

---

### Task 7: +page 移除全局人数 + 接线 onRediarize + 重订阅

**Files:**
- Modify: `desktop/src/routes/+page.svelte`

**Interfaces:**
- Consumes: `api.rediarize`(Task 4)、TranscriptView `onRediarize`(Task 6)、`subscribe`(现有)
- 移除: `expectedSpeakers` state 与 `bind:expectedSpeakers`

- [ ] **Step 1: 移除全局人数**

- 删除 `let expectedSpeakers = $state<string>("");` 及其注释。
- `submit()` 里删掉 `const n = parseInt(...)`,把提交改为不带人数:`const id = await api.submitJob(path);`(api.submitJob 第二参可选,省略即 null)。
- `<Sidebar ... />` 删除 `bind:expectedSpeakers`。

- [ ] **Step 2: 接线 onRediarize(乐观重订阅)**

在 `<TranscriptView ... onResume={...} />` 里追加 `onRediarize`:

```svelte
        onRediarize={async (n) => {
          if (!api || !selectedJobId) return;
          const id = selectedJobId;
          try {
            await api.rediarize(id, n);
          } catch (e) {
            errorBanner = `重新分人失败：${e}`;
            return;
          }
          // 乐观置为运行态并重订阅:恢复侧栏实时进度 + 面板轮询(原 done 的 WS 已摘除)
          jobs = jobs.map((jb) => (jb.id === id ? { ...jb, status: "running", progress: 0.85 } : jb));
          watching.delete(id);
          const jb = jobs.find((x) => x.id === id);
          if (jb) subscribe(jb);
        }}
```

(说明:`subscribe` 现有守卫会因 status 变为 running 而放行订阅;`watching.delete` 是为绕过"已订阅过"判定重新建连。)

- [ ] **Step 3: 类型检查 + 单测**

Run: `cd desktop && npm run check && npm test -- --run`
Expected: svelte-check 0 errors、0 warnings;vitest 全绿。

- [ ] **Step 4: 提交**

```bash
git add desktop/src/routes/+page.svelte
git commit -m "feat: +page 移除全局人数 + 接线重新分人重订阅"
```

---

### Task 8: 端到端联调(真实服务)

**Files:** 无(手动 + 冒烟)

- [ ] **Step 1: 全量测试**

```bash
cd core && venv/bin/python -m pytest -q
cd ../desktop && npm run check && npm test -- --run
```
Expected: 后端全绿;svelte-check 0/0;vitest 全绿。

- [ ] **Step 2: 起应用冒烟**(由主控在联调环节执行,非 subagent)

```bash
# 先清端口/旧进程,再起
lsof -ti tcp:1420 | xargs -r kill -9
cd desktop && WHOSAID_PYTHON=$(cd ../core && pwd)/venv/bin/python HF_ENDPOINT=https://hf-mirror.com npm run tauri dev
```

手动核对清单:
- 侧栏不再有「预计人数」框。
- 拖入音频立即开始听写;右侧面板顶部出现「人数」框,听写阶段可填;填了不报错。
- 转写完成后改人数 → 出现「重新分人」;未改名点它直接跑;改过名先弹确认。
- 重新分人时侧栏徽标显示「分人中」,面板显示「重新分人中…」,完成后说话人列表刷新、真名已清空、文字稿不变。

- [ ] **Step 3: 无回归确认**

暂停/续跑、删除二次确认、试听、导出文字稿/字幕稿仍正常。

---

## Self-Review(计划自查)

- **Spec 覆盖:** 侧栏删框→T5;右侧人数控件+状态机→T6;set_num_speakers→T1/T3;rediarize(清真名/复用闸门/幂等)→T2/T3;GET 回显→T3;新拖入流程(不带人数)→T7;重订阅→T7;分人中徽标→T5;改名确认→T6。全覆盖。
- **占位符:** 无 TBD/TODO;所有代码步给出完整代码或明确"读文件后改哪几处"。
- **类型一致:** `set_num_speakers(job_id, n)`、`rediarize(job_id, n)`、`api.setNumSpeakers(id,n)`、`api.rediarize(id,n)`、`canEditSpeakerCount(status,progress)`、`isRenamed(speakers)`、`parseCount(v)`、`onRediarize(n)` 全程一致;body 均 `{num_speakers}`;`JobDetail.num_speakers` 与 GET 返回字段名一致。
