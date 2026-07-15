# 设计:人数改为每条记录属性 + 支持只重跑「拆分人声」

日期:2026-07-15
状态:已确认,待拆实现计划

## 背景与问题

当前「预计人数」是**侧栏的全局输入框**。每场会议人数不同,放全局既易误解(填了一次以为对所有任务生效),也不符合"人数是某条录音的属性"这一事实。用户反馈要求把它挪到每条记录上。

同时厘清了一个心智模型:一条记录其实是**两件独立的事**——

1. **语音转文字**(transcribe / 听写):把音频转成带时间戳的文字段,不关心谁在说。进度 0→85%,可分块、可暂停。
2. **拆分人声**(diarize + align):用 pyannote 按说话人数把时间轴切段,再把每段文字归到对应说话人。进度 85%→100%,不可暂停。`num_speakers` **只在这一步用到**。

关键结论:文字段(text + 时间戳)一旦转好就固定并已持久化在 `job.transcript` 里。改人数**只影响"谁是谁"**,可以只重跑「拆分人声」(diarize+align),最贵的听写一秒都不用重来。

## 目标

1. 删除侧栏全局「预计人数」框。
2. 「人数」变成每条记录自己的属性,控件放在右侧详情面板顶部,跟随该记录。
3. 已完成的任务改人数后可点「重新分人」,只重跑 diarize+align,不重听。
4. 保持现有暂停/续跑、单并发闸门、持久化等不变。

## 非目标(YAGNI)

- 不做"改人数后自动重跑"(明确要显式按钮触发)。
- 不尝试跨重新分人保留真名(重新划分后"谁是谁"已变,保留会张冠李戴)。
- 不为"失败任务"单独做重新分人(失败没文字稿,走整体重来/续跑既有路径)。

## 人数控件的状态机(右侧详情面板顶部)

| 任务状态 | 「人数」框 | 「重新分人」按钮 |
|---|---|---|
| 排队 / 听写中(running & progress<0.85)/ 已暂停 / 失败 | **可改**,改完即经接口写入 job,并持久化;跑到 diarize 那步读当前值 | 不显示(尚无文字稿或尚未分过) |
| 正在分人(running & progress≥0.85) | 锁定(进行中) | 不显示 |
| 已完成(done) | 可改 | **当前值 ≠ 上次分人所用值时出现**;点击才触发 |

说明:
- 值语义:整数=约束人数;空=自动(`null`)。
- 回显:面板从 `GET /jobs/{id}` 拿到 `num_speakers` 初始化输入框。

## 「重新分人」交互(done 任务)

点「重新分人」时:
- 若**用户改过真名**(说话人列表里存在 `name != orig`),先弹二次确认(与"删除确认"同风格):
  > 重新分人会重新划分说话人,**已改的真名会被清空、需重新认领**。文字稿不受影响。确定继续?
- 若**没改过任何真名**,跳过确认直接跑。

执行后任务重回 `running`(phase=diarizing),面板显示"重新分人中…",完成后刷新说话人列表。文字稿内容不变。

## 新拖入任务的流程

拖入即开始听写(不带人数,`num_speakers=null`)。用户在听写期间到右侧面板填人数,经「改人数」接口写入 job;跑到 diarize 时读到。若没来得及填就分完了,可用「重新分人」补救。侧栏不再有任何人数输入。

## 后端改动(core)

### 1. `JobQueue.set_num_speakers(job_id, n) -> bool`(新增)

- 持锁读 job。允许写入的状态:`queued` / `paused` / `failed` 任意;`running` 仅当 `progress < 0.85`(仍在听写)。其余(`done`、正在分人的 running)返回 `False`。
- 允许时:`job.num_speakers = n`;`self._notify(job)`(触发 `store.save` 持久化);返回 `True`。
- diarize 读取处已是 `job.num_speakers or self.num_speakers`(jobs.py:216),写入后天然生效,无需改读取逻辑。

### 2. `JobQueue.rediarize(job_id, n) -> bool`(新增)

- 持锁:job 存在且 `status == "done"` 且 `transcript is not None`,否则 `False`;`job_id in _inflight` 则 `False`(复用现有幂等守卫);否则 `_inflight.add`、`job.num_speakers = n`。
- 起后台线程执行 `_run_rediarize(job)`:
  - `with _infer_gate:`(复用单并发闸门)
    - 按 `backend_factory` + `registry` 现构 backend(与 run_job 一致)。
    - `job.status="running"; job.progress=0.85; job.error=None; _emit/_notify`(此时 phase 计算为 diarizing)。
    - `segments = job.transcript.segments`(已有 text+时间戳)。
    - `turns = backend.diarize(job.audio_path, job.num_speakers or self.num_speakers)`;`progress=0.95; _emit`。
    - `labeled = align(segments, turns)`(align 只按 start/end/text 重新分配,忽略旧 speaker,生成全新 说话人A/B → 旧 rename 映射随旧 transcript 一并丢弃)。
    - `job.transcript = Transcript(segments=labeled); progress=1.0; status="done"; _emit/_notify`(持久化新结果)。
  - 异常 → `status="failed"; error=str(e); _emit/_notify`。
  - `finally:` 持锁 `_inflight.discard`。

### 3. server 接口

- `POST /jobs/{job_id}/num_speakers`,body `{num_speakers: int|null}`:调 `queue.set_num_speakers`,`False` → 409「当前不可修改人数」。
- `POST /jobs/{job_id}/rediarize`,body `{num_speakers: int|null}`:调 `queue.rediarize`,`False` → 409「仅已完成的任务可重新分人」。
- `GET /jobs/{job_id}` 响应新增 `num_speakers`(供面板回显)。

## 前端改动(desktop)

### api.ts
- `JobDetail` 增加 `num_speakers: number | null`。
- 新增 `setNumSpeakers(id, n: number | null)` → `POST .../num_speakers`。
- 新增 `rediarize(id, n: number | null)` → `POST .../rediarize`。

### Sidebar.svelte
- 删除 `expectedSpeakers` prop 与 `.spk-hint` 输入框。
- (小改)徽标:`running && progress≥0.85` 显示「分人中」而非「转写中」,避免重新分人时误导。

### +page.svelte
- 删除 `expectedSpeakers` state 与 `bind:expectedSpeakers`;`submit()` 不再带人数(提交 `null`)。
- 新增 `onRediarize(id, n)` 传给 TranscriptView:调 `api.rediarize`,成功后把该 job 乐观置为 `running`/`progress=0.85` 并 `subscribe(job)` 重订阅(恢复侧栏实时进度 + 面板轮询)。
- 重新分人的二次确认弹窗(复用现有 modal 样式),仅当面板判定"改过真名"时弹。

### TranscriptView.svelte
- 顶部新增「人数」区:数字输入框 + 状态感知(见状态机表);done 且值变动时显示「重新分人」按钮。
- 输入框改动 → 防抖调 `api.setNumSpeakers`(listening/paused/queued/failed 状态);done 状态下改动只暂存本地值,点「重新分人」才提交。
- 记录"上次分人所用人数"以判断按钮是否出现(用回显的 `num_speakers` 作基准)。
- 判断"改过真名":`speakers.some(s => s.name !== s.orig)` → 决定重新分人前是否弹确认。
- 重新分人进行中(phase=diarizing 且本任务由本次触发):禁用改名、显示"重新分人中…";轮询到 done 后刷新说话人。

## 数据 / 持久化

- `num_speakers` 已在 `Job` 字段与 `store` 存取中(现状),无需新增字段。
- `set_num_speakers` 与 `rediarize` 均经 `_notify`→`store.save` 落盘。

## 测试(TDD)

### 后端(core/tests)
- `set_num_speakers`:queued/paused/failed/running(<0.85) 允许并写入;done、running(≥0.85) 拒绝。
- `rediarize`:done 任务用新人数重跑,产出新标签且旧 rename 被清;非 done 拒绝;`_inflight` 幂等(二次调用拒绝)。
- diarize 收到的人数为新值(用假 backend 断言入参)。

### 前端(desktop vitest)
- TranscriptView:各状态下人数框可改/锁定、「重新分人」按钮出现条件。
- 改过真名 → 点重新分人先弹确认;未改名 → 不弹。

## 风险 / 注意

- 重新分人重回 running 时,原 done 的 WS 订阅已摘除;必须在前端重新订阅/重启轮询,否则界面不刷新(已在 +page onRediarize 里处理)。
- 单并发闸门下,重新分人会与其他任务的推理排队;这是既有约束,符合预期。
- 侧栏「分人中」徽标依赖 `JobSummary.progress`,该值经 WS/重订阅更新——重订阅是前提。
