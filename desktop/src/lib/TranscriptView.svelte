<script lang="ts">
  import { invoke } from "@tauri-apps/api/core";
  import SpeakerRename from "./SpeakerRename.svelte";
  import StageSwitcher from "./StageSwitcher.svelte";
  import Icon from "./Icon.svelte";
  import type { createApi, JobDetail } from "./api";
  import { canEditSpeakerCount, isRenamed, parseCount } from "./jobState";

  let {
    api,
    jobId,
    audioPath = "",
    status = "",
    onPause,
    onResume,
    onRediarize = () => {},
  }: {
    api: ReturnType<typeof createApi>;
    jobId: string;
    audioPath: string;
    // 由父组件透传的侧栏状态；变化（如转写完成）时触发重新取稿
    status: string;
    // 返回 Promise 以便感知「请求是否失败」：请求本身失败(如 409)立即恢复按钮
    onPause?: () => void | Promise<void>;
    onResume?: () => void | Promise<void>;
    // 「重新分人」提交：n 为新的人数(null=自动)。由 +page 接线，本组件单测/单独渲染时用默认空实现。
    onRediarize?: (n: number | null) => void | Promise<void>;
  } = $props();

  let detail = $state<JobDetail | null>(null);
  let loadError = $state<string | null>(null);
  let exporting = $state(false);

  // 人数控件：草稿字符串("" = 自动)、已回显同步过草稿的 jobId(防重复覆盖用户输入)、
  // 改名确认弹窗开关、非 done 态输入防抖计时器
  let countDraft = $state("");
  let countSyncedFor = $state("");
  let showRediarizeConfirm = $state(false);
  let countSaveTimer: ReturnType<typeof setTimeout> | null = null;

  // 两阶段面板：当前查看的视图段（① 转文字 / ② 分人）。用 stageSyncedFor 守卫按 jobId
  // 只做一次默认初始化，避免每秒轮询刷新 detail 时把用户手动点击 StageSwitcher 切换的
  // 选段覆盖回去（与下面 countSyncedFor 的用法同一模式）。
  let activeStage = $state<1 | 2>(1);
  let stageSyncedFor = $state("");

  // 暂停/继续的过渡态：从点击到真正生效有延迟（暂停需等当前块转完），期间按钮置灰显
  // "暂停中…/启动中…"，避免用户以为没点上而反复点击。
  let transitioning = $state<"pausing" | "resuming" | null>(null);

  // 目标状态达成即清除过渡态（由每秒轮询的 detail 驱动）
  $effect(() => {
    const st = detail?.status;
    if (transitioning === "pausing" && st === "paused") transitioning = null;
    else if (
      transitioning === "resuming" &&
      (st === "running" || st === "done" || st === "failed")
    )
      transitioning = null;
  });

  async function doPause() {
    transitioning = "pausing";
    try {
      await onPause?.();
    } catch {
      transitioning = null; // 请求即失败(如 409)则立即恢复可点
    }
  }
  async function doResume() {
    transitioning = "resuming";
    try {
      await onResume?.();
    } catch {
      transitioning = null;
    }
  }

  // 切换任务 / 父状态跳变时：立即取一次，并对「未到终态」的任务开启每秒轮询刷新。
  // 关键：WS 只驱动侧栏的 progress，主区 detail(块数/阶段/预览/稿子)不在 WS 消息里，
  // 且转写全程 status 字符串恒为 "running" 不变——若只靠 jobId/status 触发重取，主区会冻在
  // 选中那一刻(常是 0/0)，要切走再切回才更新。故运行中自行轮询，到 done/failed 停。
  $effect(() => {
    const id = jobId;
    status; // 依赖父状态：完成/失败等跳变时重建轮询
    let stopped = false;
    let timer: ReturnType<typeof setInterval> | undefined;
    detail = null;
    loadError = null;

    const tick = async () => {
      try {
        const d = await api.getJob(id);
        if (stopped || id !== jobId) return; // 已切走则丢弃这次结果，防竞态覆盖
        detail = d;
        loadError = null;
        if (d.status === "done" || d.status === "failed") clearInterval(timer);
      } catch (e) {
        if (!stopped) loadError = `${e}`;
      }
    };

    void tick(); // 立即拉一次，不等第一个轮询间隔
    timer = setInterval(tick, 1000);
    return () => {
      stopped = true;
      clearInterval(timer);
      // 切走任务时清掉未触发的人数防抖，避免其在新任务上生效（写错 job）
      if (countSaveTimer) {
        clearTimeout(countSaveTimer);
        countSaveTimer = null;
      }
    };
  });

  // 切换到新 job、或该 job 首次拿到 detail.num_speakers 时，用回显值初始化草稿；
  // 之后用户的输入不再被轮询结果覆盖（靠 countSyncedFor 只同步一次）。
  $effect(() => {
    if (detail && countSyncedFor !== jobId) {
      countDraft = detail.num_speakers != null ? String(detail.num_speakers) : "";
      countSyncedFor = jobId;
    }
  });

  // 两阶段默认视图初始化：done 任务打开时默认停在②(分人稿，现有说话人分组视图)；
  // 未 done(排队/转写中/分离中)默认停在①(转文字，此时②尚无内容)。仅在该 jobId
  // 首次拿到 detail 时生效一次，之后用户手动点 StageSwitcher 切换不会被此处覆盖。
  $effect(() => {
    if (detail && stageSyncedFor !== jobId) {
      activeStage = detail.status === "done" ? 2 : 1;
      stageSyncedFor = jobId;
    }
  });

  // 改名后手动刷新稿子与说话人列表（done 态，单次取即可）
  async function load() {
    try {
      detail = await api.getJob(jobId);
    } catch (e) {
      loadError = `${e}`;
    }
  }

  // 把 to_txt 的 "说话人X：内容\n\n" 拆成分行块（②分人稿用）
  const blocks = $derived.by(() => {
    const txt = detail?.txt ?? "";
    return txt
      .split("\n\n")
      .filter((b) => b.trim())
      .map((b) => {
        const i = b.indexOf("：");
        return i >= 0
          ? { speaker: b.slice(0, i), text: b.slice(i + 1) }
          : { speaker: "", text: b };
      });
  });

  // ①纯文字稿拆段：plain_text() 以 "\n" 拼接每个片段文本，一行即一段，无说话人标签。
  // 转写/分离进行中与 done 后①视图共用同一份数据来源与呈现方式。
  const plainBlocks = $derived.by(() => {
    const txt = detail?.plain_txt ?? "";
    return txt
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean);
  });

  // 显示名 → 原始标签映射：按原始标签分色，改名不改颜色（同一人恒定一色）
  const nameToOrig = $derived.by(() => {
    const m: Record<string, string> = {};
    for (const s of detail?.speakers ?? []) m[s.name] = s.orig;
    return m;
  });

  const PALETTE = ["#3b7ddd", "#2c8a4b", "#c0562b", "#7a4fd0", "#0e8a8a", "#b03060"];
  function colorOf(displayName: string): string {
    const key = nameToOrig[displayName] ?? displayName; // 用原始标签算色，稳定
    let h = 0;
    for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) >>> 0;
    return PALETTE[h % PALETTE.length];
  }

  // 人数框是否可编辑：分人进行中(running & progress≥0.85)锁定
  const editable = $derived(
    !!detail && canEditSpeakerCount(detail.status, detail.progress)
  );
  // done 态下，草稿人数与「上次分人所用值」不同才出现「重新分人」按钮
  const baselineCount = $derived(detail?.num_speakers != null ? String(detail.num_speakers) : "");
  const showRediarize = $derived(!!detail && detail.status === "done" && countDraft !== baselineCount);

  // 两阶段状态（供顶部 StageSwitcher）：
  // ①转文字——done 或 progress≥0.85（分人阶段代表转写早已完成）视为完成；
  // running 且 progress<0.85 为进行中；其余（queued，以及未到 0.85 的 paused/failed）为待处理。
  const stage1State = $derived.by((): "active" | "done" | "pending" => {
    if (!detail) return "pending";
    if (detail.status === "done" || detail.progress >= 0.85) return "done";
    if (detail.status === "running" && detail.progress < 0.85) return "active";
    return "pending";
  });
  // ②分人——仅 done 才算完成；running 且 progress≥0.85（含重新分人）为分离中；其余待处理。
  const stage2State = $derived.by((): "active" | "done" | "pending" => {
    if (!detail) return "pending";
    if (detail.status === "done") return "done";
    if (detail.status === "running" && detail.progress >= 0.85) return "active";
    return "pending";
  });

  // 非 done 可编辑状态：输入防抖直接写后端；done 态仅暂存草稿，等用户点「重新分人」才提交
  function onCountInput() {
    if (!detail || detail.status === "done") return;
    if (countSaveTimer) clearTimeout(countSaveTimer);
    const n = parseCount(countDraft);
    countSaveTimer = setTimeout(() => { void api.setNumSpeakers(jobId, n).catch(() => {}); }, 600);
  }

  function clickRediarize() {
    if (detail && isRenamed(detail.speakers)) showRediarizeConfirm = true;
    else void doRediarize();
  }

  async function doRediarize() {
    showRediarizeConfirm = false;
    // 不重置 countSyncedFor：草稿 countDraft 本身就是本次提交的值 n，后端 rediarize
    // 完成后会把 detail.num_speakers 写成同一个 n，届时 baselineCount 自然等于
    // countDraft，showRediarize 自动变 false。若在此重置守卫，回显同步 $effect 会在
    // 下一个 microtask（后端尚未跑完、detail 还是旧值）用旧的 num_speakers 把 countDraft
    // 覆盖回旧值，导致完成后 baselineCount 变新值而 countDraft 卡在旧值，「重新分人」
    // 按钮假性重新出现、输入框显示错误人数。
    await onRediarize(parseCount(countDraft));
  }

  function basename(p: string): string {
    const parts = p.split(/[\\/]/);
    return (parts[parts.length - 1] || "transcript").replace(/\.[^.]+$/, "");
  }

  async function onRename(orig: string, name: string) {
    await api.rename(jobId, orig, name);
    await load(); // 刷新稿子与说话人列表
  }

  async function exportAs(fmt: "txt" | "srt") {
    exporting = true;
    try {
      const text = await (await fetch(api.exportUrl(jobId, fmt))).text();
      const defaultName = `${basename(audioPath)}.${fmt}`;
      const path = await invoke<string | null>("pick_save_path", { defaultName });
      if (path) await invoke("write_file", { path, content: text });
    } catch (e) {
      loadError = `导出失败：${e}`;
    } finally {
      exporting = false;
    }
  }
</script>

<svelte:window
  onkeydown={(e) => {
    if (e.key === "Escape" && showRediarizeConfirm) showRediarizeConfirm = false;
  }}
/>

<!-- ①纯文字稿：转写中/分离中/done 但停在①段时共用，按段落只读展示，不含说话人/改名/试听 -->
{#snippet plainText()}
  {#each plainBlocks as line}
    <p class="plain-line">{line}</p>
  {/each}
{/snippet}

<div class="view">
  {#if detail}
    <StageSwitcher
      {stage1State}
      {stage2State}
      active={activeStage}
      onSelect={(n) => (activeStage = n)}
    />
    <div class="count-row">
      <label>
        人数
        <input type="number" min="1" max="20" placeholder="自动"
               bind:value={countDraft} oninput={onCountInput} disabled={!editable} />
      </label>
      {#if detail.status === "running" && detail.progress >= 0.85}
        <span class="count-hint">重新分人中…</span>
      {:else if showRediarize}
        <button class="rediarize" onclick={clickRediarize}><Icon name="refresh" size={13} /> 重新分人</button>
      {/if}
    </div>
  {/if}

  {#if loadError}
    <div class="notice error">{loadError}</div>
  {:else if !detail}
    <div class="notice">加载中…</div>
  {:else if detail.status === "failed"}
    <div class="notice error">转写失败：{detail.error ?? "未知错误"}</div>
  {:else if detail.status === "running" && detail.phase === "diarizing"}
    <div class="panel">
      <div class="fname">{basename(audioPath)}</div>
      <!-- 分人阶段(pyannote)进度粗粒度且不可中断，长录音要几分钟：不再显示会让人
           误以为卡死的静态百分比，改用不确定态流动条 + 文案说明 -->
      <p class="phase">说话人分离中…</p>
      <div class="bar indeterminate"><div class="stripe"></div></div>
      <p class="hint">说话人分离中，长录音可能需要几分钟，此步不可中断</p>
      <!-- ②分离中：主区默认显示①纯文字（转写早已完成，稿子已就绪），不必等②做完才有内容看 -->
      {#if plainBlocks.length}
        <div class="transcript plain">
          {@render plainText()}
        </div>
      {/if}
    </div>
  {:else if detail.status === "running" || detail.status === "paused"}
    <div class="panel">
      <div class="fname">{basename(audioPath)}</div>
      <div class="prog">
        <span>{detail.status === "paused" ? "已暂停" : "转写中"} {detail.chunks_done}/{detail.total_chunks} 块</span>
        <div class="bar"><div class="fill" style="width:{Math.round(detail.progress * 100)}%"></div></div>
      </div>
      {#if transitioning === "pausing"}
        <button class="ctl" disabled>暂停中…</button>
      {:else if transitioning === "resuming"}
        <button class="ctl" disabled>启动中…</button>
      {:else if detail.status === "paused"}
        <button class="ctl" onclick={doResume}><Icon name="play" size={13} /> 继续</button>
      {:else}
        <button class="ctl" onclick={doPause}><Icon name="pause" size={13} /> 暂停</button>
      {/if}
      {#if plainBlocks.length}
        <div class="preview">{@render plainText()}</div>
      {/if}
    </div>
  {:else if detail.status !== "done"}
    <div class="notice">转写中…（{Math.round(detail.progress * 100)}%）稿子完成后显示</div>
  {:else}
    <div class="toolbar">
      <div class="fname">{basename(audioPath)}</div>
      <div class="actions">
        <button disabled={exporting} title="纯文字：说话人＋内容，适合阅读/存档/复制进文档"
          onclick={() => exportAs("txt")}>导出文字稿</button>
        <button disabled={exporting} title="带时间轴字幕(SRT)：适合配录像字幕、按时间定位"
          onclick={() => exportAs("srt")}>导出字幕稿</button>
      </div>
    </div>

    {#if activeStage === 2}
      <SpeakerRename
        speakers={detail.speakers}
        {onRename}
        sampleUrl={(orig) => api.speakerSampleUrl(jobId, orig)}
      />

      <div class="transcript">
        {#each blocks as b}
          <div class="block">
            <span class="speaker" style="color:{colorOf(b.speaker)}">{b.speaker}</span>
            <span class="text">{b.text}</span>
          </div>
        {/each}
      </div>
    {:else}
      <!-- ①纯文字稿：done 但停在①段，无说话人/改名/试听，仅按段落只读展示 -->
      <div class="transcript plain">
        {@render plainText()}
      </div>
    {/if}
  {/if}

  {#if showRediarizeConfirm}
    <div
      class="modal-backdrop"
      role="presentation"
      onclick={(e) => {
        // 只在点到遮罩本身(而非冒泡自内部弹窗)时关闭，避免给内层 .modal 加事件处理器触发 a11y 警告
        if (e.target === e.currentTarget) showRediarizeConfirm = false;
      }}
    >
      <div class="modal" role="dialog" aria-modal="true">
        <div class="modal-title">重新分人</div>
        <p class="modal-body">
          重新分人会重新划分说话人，<b>已改的真名会被清空、需重新认领</b>。文字稿不受影响。确定继续？
        </p>
        <div class="modal-actions">
          <button class="btn-cancel" onclick={() => (showRediarizeConfirm = false)}>取消</button>
          <button class="btn-danger" onclick={doRediarize}>确定重新分人</button>
        </div>
      </div>
    </div>
  {/if}
</div>

<style>
  /* 全组件颜色/圆角/间距均走全局 token（tokens.css），双主题由 :root[data-theme] 统一驱动，
     组件内不再定义局部颜色回退或深色覆盖块。 */
  .view { max-width: 760px; }
  .notice {
    color: var(--muted);
    font-size: 13px;
    padding: var(--space-2) 0;
  }
  .notice.error { color: var(--danger); }
  .toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: var(--space-4);
    gap: var(--space-3);
  }
  .fname {
    font-size: 15px;
    font-weight: 600;
    color: var(--fg);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .actions { display: flex; gap: var(--space-2); flex-shrink: 0; }
  /* 导出按钮 = Apple 次按钮：描边 accent、透明底 */
  .actions button {
    padding: 6px 12px;
    border: 1px solid var(--accent);
    background: transparent;
    color: var(--accent);
    border-radius: var(--radius-btn);
    font: inherit;
    font-size: 13px;
    cursor: pointer;
    transition: background 0.15s ease, transform 0.12s ease;
  }
  .actions button:hover:not(:disabled) { background: color-mix(in srgb, var(--accent) 10%, transparent); }
  .actions button:active:not(:disabled) { transform: scale(0.97); }
  .actions button:disabled { opacity: 0.5; cursor: default; }
  .transcript { display: flex; flex-direction: column; gap: 14px; margin-top: var(--space-4); }
  .block { line-height: 1.7; }
  .speaker {
    font-weight: 600;
    font-size: 12px;
    margin-right: var(--space-2);
  }
  .text {
    color: var(--fg);
    font-size: 13px;
    white-space: pre-wrap;
  }
  /* ①纯文字稿：按段落展示，无说话人标签 */
  .transcript.plain { gap: 10px; }
  .plain-line {
    margin: 0;
    color: var(--fg);
    font-size: 13px;
    line-height: 1.7;
    white-space: pre-wrap;
  }

  .panel {
    border: 1px solid var(--hairline);
    border-radius: var(--radius-card);
    padding: var(--space-4) 18px;
    background: var(--card);
  }
  .phase {
    color: var(--muted);
    font-size: 13px;
    margin: var(--space-2) 0 0;
  }
  .hint {
    color: var(--muted);
    font-size: 12px;
    margin: var(--space-2) 0 0;
  }
  /* 分人阶段不确定态进度条：一条高亮色块来回滑动，替代停在 85% 的固定宽度 */
  .bar.indeterminate { position: relative; overflow: hidden; margin-top: var(--space-2); }
  .bar.indeterminate .stripe {
    position: absolute;
    top: 0;
    left: 0;
    height: 100%;
    width: 40%;
    background: var(--accent);
    border-radius: 3px;
    animation: indeterminate 1.15s ease-in-out infinite;
  }
  @keyframes indeterminate {
    0% { transform: translateX(-110%); }
    100% { transform: translateX(360%); }
  }
  @media (prefers-reduced-motion: reduce) {
    .bar.indeterminate .stripe { animation: none; }
  }
  .prog {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin: var(--space-2) 0;
    font-size: 13px;
    color: var(--muted);
  }
  .bar {
    height: 6px;
    border-radius: 3px;
    background: var(--hairline);
    overflow: hidden;
  }
  .fill {
    height: 100%;
    background: var(--accent);
    transition: width 0.3s ease;
  }
  /* 暂停/继续 = Apple 主按钮：实心 accent、白字、圆角 6、按压缩放 */
  .ctl {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 14px;
    border: 1px solid var(--accent);
    background: var(--accent);
    color: #fff;
    border-radius: var(--radius-btn);
    font: inherit;
    font-size: 13px;
    cursor: pointer;
    transition: opacity 0.15s ease, transform 0.12s ease;
  }
  .ctl:hover:not(:disabled) { opacity: 0.9; }
  .ctl:active:not(:disabled) { transform: scale(0.97); }
  .ctl:disabled { opacity: 0.5; cursor: default; }
  .preview {
    margin-top: var(--space-4);
    padding-top: var(--space-3);
    border-top: 1px dashed var(--hairline);
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .preview .plain-line { font-size: 13px; color: var(--fg); }

  /* 人数控件区 */
  .count-row {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    margin: var(--space-3) 0 var(--space-4);
    font-size: 13px;
    color: var(--muted);
  }
  .count-row label {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  /* Apple 输入框：细描边、圆角 6，聚焦时用 --focus 焦点环 */
  .count-row input {
    width: 54px;
    padding: 4px 6px;
    border: 1px solid var(--hairline);
    background: var(--card);
    color: var(--fg);
    border-radius: var(--radius-btn);
    font: inherit;
    font-size: 13px;
    transition: border-color 0.15s ease;
  }
  .count-row input:focus-visible {
    outline: 2px solid var(--focus);
    outline-offset: 1px;
  }
  .count-row input:disabled { opacity: 0.5; cursor: default; }
  .count-hint { color: var(--muted); }
  .rediarize {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 5px 12px;
    border: 1px solid var(--accent);
    background: var(--accent);
    color: #fff;
    border-radius: var(--radius-btn);
    font: inherit;
    font-size: 12px;
    cursor: pointer;
    transition: opacity 0.15s ease, transform 0.12s ease;
  }
  .rediarize:hover { opacity: 0.9; }
  .rediarize:active { transform: scale(0.97); }

  /* 改名确认弹窗：token 化，与 +page.svelte 的删除确认弹窗视觉保持同一套规范
     （圆角 12 / 柔和阴影 / 遮罩 40% 黑），仅浮层允许用阴影表达深度 */
  .modal-backdrop {
    position: fixed;
    inset: 0;
    z-index: 20;
    background: rgba(0, 0, 0, 0.4);
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .modal {
    width: 340px;
    max-width: 90vw;
    background: var(--card);
    border-radius: var(--radius-modal);
    padding: var(--space-5);
    box-shadow: 0 8px 30px rgba(0, 0, 0, 0.16);
  }
  .modal-title {
    font-size: 15px;
    font-weight: 600;
    color: var(--fg);
    margin-bottom: var(--space-2);
  }
  .modal-body {
    font-size: 13px;
    line-height: 1.7;
    color: var(--muted);
    margin: 0 0 var(--space-4);
  }
  .modal-body b { color: var(--danger); }
  .modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: var(--space-2);
  }
  .modal-actions button {
    padding: 7px 16px;
    border-radius: var(--radius-btn);
    font: inherit;
    font-size: 13px;
    cursor: pointer;
    border: 1px solid transparent;
    transition: transform 0.12s ease, border-color 0.15s ease, background 0.15s ease;
  }
  .modal-actions button:active { transform: scale(0.97); }
  .modal-actions button:focus-visible {
    outline: 2px solid var(--focus);
    outline-offset: 1px;
  }
  .btn-cancel {
    background: transparent;
    border-color: var(--hairline);
    color: var(--fg);
  }
  .btn-cancel:hover { border-color: var(--muted); }
  .btn-danger {
    background: var(--danger);
    color: #fff;
  }
  .btn-danger:hover { opacity: 0.9; }
</style>
