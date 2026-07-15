<script lang="ts">
  import { invoke } from "@tauri-apps/api/core";
  import SpeakerRename from "./SpeakerRename.svelte";
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

  // 改名后手动刷新稿子与说话人列表（done 态，单次取即可）
  async function load() {
    try {
      detail = await api.getJob(jobId);
    } catch (e) {
      loadError = `${e}`;
    }
  }

  // 把 to_txt 的 "说话人X：内容\n\n" 拆成分行块
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

  // 非 done 可编辑状态：输入防抖直接写后端；done 态仅暂存草稿，等用户点「重新分人」才提交
  function onCountInput() {
    if (!detail || detail.status === "done") return;
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
    countSyncedFor = ""; // 允许下一轮轮询用新的回显值重新同步草稿
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

<div class="view">
  {#if detail}
    <div class="count-row">
      <label>
        人数
        <input type="number" min="1" max="20" placeholder="自动"
               bind:value={countDraft} oninput={onCountInput} disabled={!editable} />
      </label>
      {#if detail.status === "running" && detail.progress >= 0.85}
        <span class="count-hint">重新分人中…</span>
      {:else if showRediarize}
        <button class="rediarize" onclick={clickRediarize}>重新分人</button>
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
      <p class="phase">说话人分离中…（{Math.round(detail.progress * 100)}%）</p>
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
        <button class="ctl" onclick={doResume}>继续</button>
      {:else}
        <button class="ctl" onclick={doPause}>暂停</button>
      {/if}
      {#if detail.txt}<div class="preview">{detail.txt}</div>{/if}
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
  {/if}

  {#if showRediarizeConfirm}
    <div class="modal-backdrop" role="presentation">
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
  .view { max-width: 760px; }
  .notice {
    color: var(--muted, #8a8a90);
    font-size: 14px;
    padding: 8px 0;
  }
  .notice.error { color: #cf3b3b; }
  .toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 16px;
    gap: 12px;
  }
  .fname {
    font-size: 15px;
    font-weight: 600;
    color: var(--fg, #1a1a1a);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .actions { display: flex; gap: 8px; flex-shrink: 0; }
  .actions button {
    padding: 6px 12px;
    border: 1px solid var(--line, #d8d8dc);
    background: var(--card, #fff);
    color: var(--fg, #333);
    border-radius: 6px;
    font: inherit;
    font-size: 13px;
    cursor: pointer;
  }
  .actions button:hover:not(:disabled) { border-color: var(--accent, #3b7ddd); }
  .actions button:disabled { opacity: 0.5; cursor: default; }
  .transcript { display: flex; flex-direction: column; gap: 14px; }
  .block { line-height: 1.7; }
  .speaker {
    font-weight: 600;
    font-size: 13px;
    margin-right: 8px;
  }
  .text {
    color: var(--fg, #222);
    font-size: 14px;
    white-space: pre-wrap;
  }

  .panel {
    border: 1px solid var(--line, #e8e8ec);
    border-radius: 10px;
    padding: 16px 18px;
    background: var(--card, #fafafa);
  }
  .phase {
    color: var(--muted, #8a8a90);
    font-size: 14px;
    margin: 10px 0 0;
  }
  .prog {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin: 10px 0;
    font-size: 13px;
    color: var(--muted, #6a6a70);
  }
  .bar {
    height: 6px;
    border-radius: 3px;
    background: var(--line, #e0e0e4);
    overflow: hidden;
  }
  .fill {
    height: 100%;
    background: var(--accent, #3b7ddd);
    transition: width 0.3s ease;
  }
  .ctl {
    padding: 6px 14px;
    border: 1px solid var(--accent, #3b7ddd);
    background: var(--accent, #3b7ddd);
    color: #fff;
    border-radius: 6px;
    font: inherit;
    font-size: 13px;
    cursor: pointer;
  }
  .ctl:hover:not(:disabled) { opacity: 0.9; }
  .ctl:disabled { opacity: 0.5; cursor: default; }
  .preview {
    margin-top: 14px;
    padding-top: 12px;
    border-top: 1px dashed var(--line, #e0e0e4);
    color: var(--fg, #444);
    font-size: 13px;
    white-space: pre-wrap;
    line-height: 1.6;
  }

  /* 人数控件区 */
  .count-row {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 14px;
    font-size: 13px;
    color: var(--muted, #6a6a70);
  }
  .count-row label {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .count-row input {
    width: 54px;
    padding: 4px 6px;
    border: 1px solid var(--line, #d8d8dc);
    background: var(--card, #fff);
    color: var(--fg, #1a1a1a);
    border-radius: 6px;
    font: inherit;
    font-size: 13px;
  }
  .count-row input:disabled { opacity: 0.5; cursor: default; }
  .count-hint { color: var(--muted, #8a8a90); }
  .rediarize {
    padding: 5px 12px;
    border: 1px solid var(--accent, #3b7ddd);
    background: var(--accent, #3b7ddd);
    color: #fff;
    border-radius: 6px;
    font: inherit;
    font-size: 12px;
    cursor: pointer;
  }
  .rediarize:hover { opacity: 0.9; }

  /* 改名确认弹窗：样式与 +page.svelte 的删除确认弹窗保持一致 */
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
    background: #fff;
    border-radius: 12px;
    padding: 20px;
    box-shadow: 0 12px 40px rgba(0, 0, 0, 0.25);
  }
  .modal-title {
    font-size: 15px;
    font-weight: 600;
    color: #1a1a1a;
    margin-bottom: 10px;
  }
  .modal-body {
    font-size: 13px;
    line-height: 1.7;
    color: #4a4a4a;
    margin: 0 0 18px;
  }
  .modal-body b { color: #cf3b3b; }
  .modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: 10px;
  }
  .modal-actions button {
    padding: 7px 16px;
    border-radius: 7px;
    font: inherit;
    font-size: 13px;
    cursor: pointer;
    border: 1px solid transparent;
  }
  .btn-cancel {
    background: transparent;
    border-color: #d8d8dc;
    color: #333;
  }
  .btn-cancel:hover { border-color: #b0b0b6; }
  .btn-danger {
    background: #cf3b3b;
    color: #fff;
  }
  .btn-danger:hover { background: #b83232; }

  @media (prefers-color-scheme: dark) {
    .view { --line: #2a2a2e; --card: #232327; --fg: #eaeaea; --muted: #8a8a90; }
    .modal { background: #26262a; box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5); }
    .modal-title { color: #eaeaea; }
    .modal-body { color: #c4c4c8; }
    .btn-cancel { border-color: #3a3a40; color: #d0d0d4; }
    .btn-cancel:hover { border-color: #55555c; }
  }
</style>
