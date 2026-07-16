# Apple 风格重塑 + 两阶段面板 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 把 whosaid 桌面 app 重塑为 Apple/macOS 原生视觉,并把详情面板改成"两阶段(①转文字/②分人)"的 macOS 分段控件——进行中显进度、完成后可分别查看纯文字稿与分人稿。

**Architecture:** 先建一套全局 Apple 设计 token(CSS 变量,双主题)与内联 SVG 图标模块作基座;各组件逐个 token 化 + 控件重塑;后端 `GET /jobs/{id}` 增 `plain_txt` 只读字段;详情面板加分段控件 `StageSwitcher` 驱动 ①/② 视图;最后加真 macOS vibrancy(Rust window-vibrancy)。

**Tech Stack:** Tauri 2 + Rust(vibrancy);SvelteKit + Svelte 5 runes + TypeScript + vitest;FastAPI(core)。

## Global Constraints

- 设计规范以 spec 为准:`docs/superpowers/specs/2026-07-16-apple-restyle-and-two-stage-panel-design.md`(颜色/字体/圆角/间距/控件取值 verbatim 照它)。
- **只动展示层**,不改转写/暂停/续跑/重新分人/试听/删除/模型切换等业务逻辑。
- 双主题(浅/深,靠 `document.documentElement[data-theme]`)都要正确;每个前端 task 结束必须 `cd desktop && npm run check`(**0 errors 0 warnings**)+ `npm test -- --run`(**≥11 passed**)。
- 后端改动 pytest 在 `core/` 下跑:`cd core && venv/bin/python -m pytest -q`。
- 中文注释。不引入运行时新前端依赖(图标用内联 SVG,字体用 `-apple-system` 系统栈,无 webfont)。
- Apple token 关键值:accent `#0066cc`(深 `#0a84ff`)、focus `#0071e3`、parchment `#f5f5f7`(深 `#252527`)、ink `#1d1d1f`(深 `#f5f5f7`)、muted `#7a7a7a`(深 `#98989d`)、hairline `#e0e0e0`(深 `#3a3a3c`)、danger `#ff3b30`(深 `#ff453a`);圆角 按钮6/卡片10/弹窗12/分段7;正文 13px、次要 12px、小标题 15/600、大标题 20/600;按压 `scale(0.97)`;间距 4/8/12/16/24/32。
- 完成任务详情默认视图 = ② 分人稿。

---

### Task 1: 全局 Apple 设计 token(双主题基座)

**Files:**
- Create: `desktop/src/lib/tokens.css`
- Modify: `desktop/src/routes/+page.svelte`(引入 token、字体栈、全局背景/前景改用 token)

**Interfaces:**
- Produces: 一套 `:root` CSS 变量(见 Global Constraints 取值),深色经 `:root[data-theme="dark"]` 覆盖。变量名:`--accent --focus --bg --sidebar-bg --card --fg --muted --hairline --danger --radius-btn --radius-card --radius-modal --radius-seg --space-1..6`。

- [ ] **Step 1: 建 tokens.css**,按 spec/Global Constraints 定义浅色 `:root{...}` 与 `:root[data-theme="dark"]{...}` 两套变量(颜色 + 圆角 + 间距),并定义字体变量 `--font: -apple-system, "SF Pro Text", "SF Pro Display", system-ui, sans-serif;`。

- [ ] **Step 2: 在 +page.svelte 引入**:`<script>` 顶部 `import "$lib/tokens.css";`。把现有 `:global(html, body)` 的硬编码色改为 `background: var(--bg); color: var(--fg); font-family: var(--font);`,深色媒体查询那段改成依赖 token(删掉重复的深色硬编码,由 token 统一)。正文默认 13px。

- [ ] **Step 3: 验证** `cd desktop && npm run check && npm test -- --run` → 0/0、11 passed。启动前不必,类型/测试过即可。

- [ ] **Step 4: 提交** `git add desktop/src/lib/tokens.css desktop/src/routes/+page.svelte && git commit -m "feat(ui): Apple 设计 token 基座(双主题)"`

---

### Task 2: 内联 SVG 图标模块(替换 emoji)

**Files:**
- Create: `desktop/src/lib/Icon.svelte`

**Interfaces:**
- Produces: `<Icon name="gear|moon|sun|close|play|pause|check|chevron-down|refresh" size={16} />`,输出 `stroke="currentColor"` 的线性 SVG(stroke-width 1.5,`aria-hidden` 由调用方按需加 label)。

- [ ] **Step 1: 建 Icon.svelte**:`let { name, size = 16 }: { name: string; size?: number } = $props();` 内部一个 `paths` 映射(SF Symbols 风的简洁线性路径)。用 `viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"`。至少含:gear(设置)、moon、sun、close(✕)、play(▶)、pause(⏸)、check(✓)、chevron-down、refresh(重新分人)。宽高 = size。

- [ ] **Step 2: 验证** `cd desktop && npm run check` → 0/0(此任务不接线,仅新增组件,check 应过)。

- [ ] **Step 3: 提交** `git add desktop/src/lib/Icon.svelte && git commit -m "feat(ui): 内联 SVG 线性图标模块"`

---

### Task 3: Sidebar 重塑(Finder 选中行 + token + 图标)

**Files:**
- Modify: `desktop/src/lib/Sidebar.svelte`

- [ ] **Step 1: 先读 Sidebar.svelte 全文**(含现有 token 变量、`badgeFor`、分人中流动条、分组、`.job` 选中态、模型管理/主题按钮)。
- [ ] **Step 2:** 把组件内 `<style>` 的所有硬编码色改用全局 token(`var(--sidebar-bg)`/`--fg`/`--muted`/`--hairline`/`--accent`/`--card` 等);删组件内自定义 dark 覆盖(改由全局 token 提供)。
- [ ] **Step 3:** 选中行 `.job.selected` 改成 **Finder 式实心 accent 圆角高亮 + 白字**(名字/徽标在选中时反白);未选中为透明/hover 浅灰。
- [ ] **Step 4:** 徽标(badge)改 Apple 风小胶囊(圆角、低饱和底);「分人中」流动条颜色用 `--accent`。
- [ ] **Step 5:** 「⚙ 模型管理」「🌙/☀️」按钮换 `<Icon name="gear"/>`、`<Icon name={currentTheme==='dark'?'sun':'moon'}/>`,删除按钮 ✕ 换 `<Icon name="close"/>`;图标按钮加 `aria-label`。
- [ ] **Step 6:** 验证 `cd desktop && npm run check && npm test -- --run` → 0/0、11 passed。
- [ ] **Step 7:** 提交 `git add desktop/src/lib/Sidebar.svelte && git commit -m "feat(ui): Sidebar Apple 化(Finder 选中行/token/SVG 图标)"`

---

### Task 4: 后端 + api 暴露纯文字稿字段 `plain_txt`

**Files:**
- Modify: `core/transcribe_core/server.py`(get_job 返回加 `plain_txt`)
- Modify: `desktop/src/lib/api.ts`(JobDetail 加 `plain_txt: string`)
- Test: `core/tests/test_server.py`(追加断言 done 任务返回 `plain_txt` 为纯文字、无「说话人X：」前缀)

**Interfaces:**
- Produces:`GET /jobs/{id}` 响应新增 `plain_txt`(始终 `job.transcript.plain_text()`,transcript 为 None 时空串);`txt` 语义不变。

- [ ] **Step 1: 写失败测试**(追加 test_server.py):提交并 `_wait_done`,断言 `resp["plain_txt"]` 非空且不含 `"："`(纯文字不分组;FakeBackend 段文本 "你好"/"在吗" 拼接无冒号)。
- [ ] **Step 2:** 跑确认失败:`cd core && venv/bin/python -m pytest tests/test_server.py -k plain_txt -q` → KeyError/FAIL。
- [ ] **Step 3: 实现**:get_job 里 `plain = j.transcript.plain_text() if j.transcript is not None else ""`,return dict 加 `"plain_txt": plain`。api.ts `JobDetail` 接口加 `plain_txt: string;`。
- [ ] **Step 4:** 跑通:`cd core && venv/bin/python -m pytest -q`(应 88+1 passed)、`cd ../desktop && npm run check`(0/0)。
- [ ] **Step 5:** 提交 `git add core/transcribe_core/server.py core/tests/test_server.py desktop/src/lib/api.ts && git commit -m "feat: get_job 暴露 plain_txt 纯文字稿字段"`

---

### Task 5: StageSwitcher 分段控件(macOS 风)

**Files:**
- Create: `desktop/src/lib/StageSwitcher.svelte`

**Interfaces:**
- Produces:`<StageSwitcher stage1State stage2State active onSelect />`
  - props:`stage1State: "active"|"done"|"pending"`、`stage2State: "active"|"done"|"pending"`、`active: 1|2`(当前选中段)、`onSelect: (n: 1|2) => void`。
  - 两段:「① 转文字」「② 分人」。`done` 段旁显 `<Icon name="check"/>` 且**可点**(onSelect);`active` 段高亮/进行中(② active 时段内显小流动点或 spinner);`pending` 段置灰不可点。
  - macOS 风:浅灰容器(`--muted`/hairline 底)+ 选中段白色凸起(深色下深灰凸起)+ 细阴影 + 圆角 `--radius-seg`。按压 `scale(0.97)`。

- [ ] **Step 1: 建 StageSwitcher.svelte**,按上面接口实现;各状态 CSS 用 token;可点段 `cursor: pointer` + `aria-label`;`pending`/`active` 不触发 onSelect。
- [ ] **Step 2:** 验证 `cd desktop && npm run check` → 0/0。
- [ ] **Step 3:** 提交 `git add desktop/src/lib/StageSwitcher.svelte && git commit -m "feat(ui): macOS 风两阶段分段控件 StageSwitcher"`

---

### Task 6: TranscriptView 两阶段面板 + ①/②视图切换(核心)

**Files:**
- Modify: `desktop/src/lib/TranscriptView.svelte`

**Interfaces:**
- Consumes:`StageSwitcher`(T5)、`JobDetail.plain_txt`(T4)、`Icon`(T2)、全局 token(T1)。

- [ ] **Step 1: 先读 TranscriptView.svelte 全文**(现有:轮询 detail、人数控件、暂停/继续、重新分人、导出、SpeakerRename、分离中流动条与提示)。
- [ ] **Step 2: 计算两段状态**(派生):
  - `stage1State`:`detail.status==="done" || detail.progress>=0.85` → `"done"`;`running && progress<0.85` → `"active"`;else(queued)→ `"pending"`(paused/failed 按 progress 归类:≥0.85 视 done,否则 active/pending)。
  - `stage2State`:`done` → `"done"`;`running && progress>=0.85`(含重新分人)→ `"active"`;else `"pending"`。
  - `activeStage: 1|2`,`$state`;默认:done 任务初始化为 `2`,未 done 时随阶段(转写中=1,分离中=1 但可切,展示纯文字)。用一个 `stageSyncedFor` 守卫按 jobId 初始化一次(参照现有 countSyncedFor 模式,避免覆盖用户手动切换)。
- [ ] **Step 3: 顶部渲染 `<StageSwitcher>`**,传入上面派生值;`onSelect={(n)=>activeStage=n}`(仅 `done` 段可点,StageSwitcher 内部已挡 pending/active)。人数控件与导出按钮位置保留。
- [ ] **Step 4: 主区按 activeStage 切换渲染**:
  - `activeStage===1`:渲染**纯文字稿** = `detail.plain_txt`(按段/换行的只读文本,无说话人、无改名、无试听)。转写中也走这条(流式纯文字)。
  - `activeStage===2`:渲染**分人稿** = 现有说话人分组视图(SpeakerRename + 改名 + 试听);仅 done 有意义。
  - 分离中(stage2 active)时,主区默认显示 ①纯文字 + 分离中提示(复用现有流动条/提示);② 段为 active 不可点。
- [ ] **Step 5:** 全组件 `<style>` token 化 + 控件 Apple 化(按钮圆角6/按压缩放、导出按钮次按钮样式、人数框 Apple 输入样式);✕/▶/⏸ 等 emoji 换 `<Icon>`(试听播放按钮的 ▶/⏸/spinner)。**不改**暂停/续跑/重新分人/试听/轮询逻辑。
- [ ] **Step 6:** 验证 `cd desktop && npm run check && npm test -- --run` → 0/0、11 passed。
- [ ] **Step 7:** 提交 `git add desktop/src/lib/TranscriptView.svelte && git commit -m "feat(ui): 详情面板两阶段面板 + 纯文字/分人稿切换 + Apple 化"`

---

### Task 7: ModelManager + SpeakerRename 重塑

**Files:**
- Modify: `desktop/src/lib/ModelManager.svelte`
- Modify: `desktop/src/lib/SpeakerRename.svelte`

- [ ] **Step 1: 先分别读两文件全文。**
- [ ] **Step 2: ModelManager**:token 化(卡片 `--card`/hairline、圆角10);「下载/设为当前/已下载」按钮改 Apple 主/次/成功态;关闭 ✕ 换 `<Icon name="close"/>`;列表行 hover/选中态 Apple 化;标题字号按 token。
- [ ] **Step 3: SpeakerRename**:token 化;试听播放按钮 ▶/⏸/spinner 换 `<Icon>`(play/pause,loading 用 spinner CSS);输入框 Apple 样式;**不改**播放状态机/改名逻辑。
- [ ] **Step 4:** 验证 `cd desktop && npm run check && npm test -- --run` → 0/0、11 passed。
- [ ] **Step 5:** 提交 `git add desktop/src/lib/ModelManager.svelte desktop/src/lib/SpeakerRename.svelte && git commit -m "feat(ui): 模型管理与试听改名 Apple 化"`

---

### Task 8: 真 macOS vibrancy(Rust)+ 侧栏透出 + 收尾验证

**Files:**
- Modify: `desktop/src-tauri/Cargo.toml`(加 `window-vibrancy` 依赖)
- Modify: `desktop/src-tauri/src/lib.rs`(或 main.rs;Tauri setup 里 apply_vibrancy)
- Modify: `desktop/src-tauri/tauri.conf.json`(窗口 transparent + macOSPrivateApi)
- Modify: `desktop/src/routes/+page.svelte` / `Sidebar.svelte`(html/body 透明、侧栏底透明、主区不透明)

- [ ] **Step 1: 先读** `src-tauri/Cargo.toml`、`src-tauri/src/lib.rs`(找 `tauri::Builder ... .setup(...)` 或 run 入口)、`tauri.conf.json` 的 window 配置,确认版本与结构。
- [ ] **Step 2: Cargo.toml** 加依赖(macOS 平台):`window-vibrancy = "0.5"`(以 crates.io 当前兼容 tauri2 的版本为准;`cargo` 解析失败则取最新兼容版)。
- [ ] **Step 3: setup 里应用**(仅 macOS,cfg 守卫):
  ```rust
  #[cfg(target_os = "macos")]
  {
      use window_vibrancy::{apply_vibrancy, NSVisualEffectMaterial};
      let win = app.get_webview_window("main").unwrap();
      let _ = apply_vibrancy(&win, NSVisualEffectMaterial::Sidebar, None, None);
  }
  ```
  (`app` 为 setup 闭包的 `&mut App`;窗口 label 以 tauri.conf.json 为准,通常 `main`。)
- [ ] **Step 4: tauri.conf.json** 主窗口加 `"transparent": true`;`app.macOSPrivateApi: true`(v2 位置以现有结构为准)。
- [ ] **Step 5: CSS**:`:global(html, body)` 背景改 `transparent`;`.sidebar` 背景改透明(或极低不透明度让 vibrancy 透出),保留发丝右分隔线;`.content`(主区)保持不透明 `var(--bg)`。深浅都要正常。
- [ ] **Step 6: 构建验证**:`cd desktop && npm run check`(0/0)+ 让主控在联调环节 `npm run tauri dev` 重新编译起应用,肉眼确认侧栏磨砂、深浅切换、两阶段面板、各页视觉。Rust 编译失败则回报(BLOCKED)。
- [ ] **Step 7:** 提交 `git add desktop/src-tauri/Cargo.toml desktop/src-tauri/src/lib.rs desktop/src-tauri/tauri.conf.json desktop/src/routes/+page.svelte desktop/src/lib/Sidebar.svelte && git commit -m "feat(ui): 真 macOS vibrancy 磨砂侧栏"`

---

## Self-Review(计划自查)
- **Spec 覆盖**:token 基座→T1;图标→T2;Sidebar→T3;plain_txt→T4;分段控件→T5;两阶段面板+①/②切换→T6;ModelManager/SpeakerRename→T7;vibrancy→T8。全覆盖。
- **顺序/依赖**:T1 token、T2 图标为基座,先行;T4(后端字段)供 T6;T5(分段控件)供 T6;T8 vibrancy 最后(需重编译)。每个 task 结束都能 `npm run check` 通过(新增组件不接线也能过)。
- **类型一致**:`Icon name/size`、`StageSwitcher stage1State/stage2State/active/onSelect`、`JobDetail.plain_txt`、token 变量名全程一致。
- **风险**:T8 涉 Rust + 窗口透明,可能需按实际 tauri2 API/版本微调;失败即 BLOCKED 上报,不硬闯。
