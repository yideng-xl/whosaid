<script lang="ts">
  import type { JobSummary } from "./api";

  // 呈现型组件：任务列表由 +page 统一持有并传入，本组件只负责渲染与派发点击。
  let {
    jobs = [],
    selectedJobId = null,
    dragging = false,
    onSelect,
    onOpenModels,
    onDelete,
  }: {
    jobs: JobSummary[];
    selectedJobId: string | null;
    dragging: boolean;
    onSelect: (id: string) => void;
    onOpenModels: () => void;
    onDelete: (id: string) => void;
  } = $props();

  function basename(p: string): string {
    const parts = p.split(/[\\/]/);
    return parts[parts.length - 1] || p;
  }

  const STATUS: Record<string, { label: string; cls: string }> = {
    queued: { label: "排队中", cls: "queued" },
    running: { label: "转写中", cls: "running" },
    done: { label: "完成", cls: "done" },
    failed: { label: "失败", cls: "failed" },
    paused: { label: "已暂停", cls: "paused" },
  };

  function statusOf(s: string) {
    return STATUS[s] ?? { label: s, cls: "queued" };
  }
</script>

<aside class="sidebar">
  <div class="brand">whosaid</div>

  <div class="drop-hint" class:active={dragging}>
    把音频文件拖进窗口开始转写
  </div>

  <div class="job-list">
    {#if jobs.length === 0}
      <div class="empty">还没有任务</div>
    {/if}
    {#each jobs as job (job.id)}
      <button
        class="job"
        class:selected={job.id === selectedJobId}
        onclick={() => onSelect(job.id)}
      >
        <div class="job-top">
          <span class="name" title={job.audio_path}>{basename(job.audio_path)}</span>
          <span class="badge {statusOf(job.status).cls}">{statusOf(job.status).label}</span>
          {#if job.status !== "running" && job.status !== "queued"}
            <button class="del" title="删除任务" onclick={(e) => { e.stopPropagation(); onDelete(job.id); }}>✕</button>
          {/if}
        </div>
        {#if job.status === "running" || job.status === "queued"}
          <div class="bar"><div class="fill" style="width:{Math.round(job.progress * 100)}%"></div></div>
        {/if}
        {#if job.status === "failed" && job.error}
          <div class="err">{job.error}</div>
        {/if}
      </button>
    {/each}
  </div>

  <button class="models-entry" onclick={onOpenModels}>⚙ 模型管理</button>
</aside>

<style>
  .sidebar {
    width: 260px;
    min-width: 260px;
    height: 100vh;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    border-right: 1px solid var(--line, #e3e3e6);
    background: var(--side-bg, #fafafa);
    padding: 12px;
    gap: 10px;
  }
  .brand {
    font-weight: 600;
    font-size: 14px;
    color: var(--fg, #1a1a1a);
    padding: 4px 2px;
  }
  .drop-hint {
    border: 1.5px dashed var(--line, #cfcfd4);
    border-radius: 10px;
    padding: 14px 10px;
    text-align: center;
    font-size: 12px;
    color: var(--muted, #8a8a90);
    transition: all 0.15s;
  }
  .drop-hint.active {
    border-color: var(--accent, #3b7ddd);
    background: color-mix(in srgb, var(--accent, #3b7ddd) 10%, transparent);
    color: var(--accent, #3b7ddd);
  }
  .job-list {
    flex: 1;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .empty {
    color: var(--muted, #a0a0a6);
    font-size: 12px;
    text-align: center;
    padding: 20px 0;
  }
  .job {
    text-align: left;
    background: var(--card, #fff);
    border: 1px solid var(--line, #e8e8ec);
    border-radius: 8px;
    padding: 8px 10px;
    cursor: pointer;
    font: inherit;
    color: var(--fg, #1a1a1a);
    transition: border-color 0.12s, background 0.12s;
  }
  .job:hover { border-color: var(--accent, #3b7ddd); }
  .job.selected {
    border-color: var(--accent, #3b7ddd);
    background: color-mix(in srgb, var(--accent, #3b7ddd) 8%, var(--card, #fff));
  }
  .job-top { display: flex; align-items: center; gap: 8px; }
  .name {
    flex: 1;
    font-size: 13px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .badge {
    font-size: 11px;
    padding: 1px 7px;
    border-radius: 999px;
    white-space: nowrap;
  }
  .badge.queued { background: #eee; color: #666; }
  .badge.running { background: #e5efff; color: #2f6fd0; }
  .badge.done { background: #e3f6e8; color: #2c8a4b; }
  .badge.failed { background: #fde8e8; color: #cf3b3b; }
  .badge.paused { background: #fdf0dc; color: #b9711a; }
  .del {
    flex-shrink: 0;
    background: transparent;
    border: none;
    padding: 0 2px;
    font-size: 12px;
    line-height: 1;
    color: var(--muted, #a0a0a6);
    cursor: pointer;
    opacity: 0;
    transition: opacity 0.12s, color 0.12s;
  }
  .job:hover .del { opacity: 1; }
  .del:hover { color: #cf3b3b; }
  .bar {
    margin-top: 6px;
    height: 4px;
    border-radius: 3px;
    background: var(--line, #ececf0);
    overflow: hidden;
  }
  .fill {
    height: 100%;
    background: var(--accent, #3b7ddd);
    transition: width 0.25s;
  }
  .err {
    margin-top: 4px;
    font-size: 11px;
    color: #cf3b3b;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .models-entry {
    text-align: left;
    background: transparent;
    border: none;
    border-top: 1px solid var(--line, #e8e8ec);
    padding: 10px 4px 2px;
    cursor: pointer;
    font: inherit;
    font-size: 13px;
    color: var(--muted, #6a6a70);
  }
  .models-entry:hover { color: var(--accent, #3b7ddd); }

  @media (prefers-color-scheme: dark) {
    .sidebar { --line: #2a2a2e; --side-bg: #1b1b1e; --fg: #eaeaea; --muted: #8a8a90; --card: #232327; }
    .badge.queued { background: #333; color: #bbb; }
    .badge.paused { background: #4a3416; color: #e3a44b; }
  }
</style>
