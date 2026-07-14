<script lang="ts">
  import { invoke } from "@tauri-apps/api/core";
  import SpeakerRename from "./SpeakerRename.svelte";
  import type { createApi, JobDetail } from "./api";

  let {
    api,
    jobId,
    audioPath = "",
    status = "",
  }: {
    api: ReturnType<typeof createApi>;
    jobId: string;
    audioPath: string;
    // 由父组件透传的侧栏状态；变化（如转写完成）时触发重新取稿
    status: string;
  } = $props();

  let detail = $state<JobDetail | null>(null);
  let loadError = $state<string | null>(null);
  let exporting = $state(false);

  // jobId 变化、或状态变化（转写中→完成）时重新取稿
  $effect(() => {
    jobId;
    status;
    void load();
  });

  async function load() {
    detail = null;
    loadError = null;
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
  {#if loadError}
    <div class="notice error">{loadError}</div>
  {:else if !detail}
    <div class="notice">加载中…</div>
  {:else if detail.status === "failed"}
    <div class="notice error">转写失败：{detail.error ?? "未知错误"}</div>
  {:else if detail.status !== "done"}
    <div class="notice">转写中…（{Math.round(detail.progress * 100)}%）稿子完成后显示</div>
  {:else}
    <div class="toolbar">
      <div class="fname">{basename(audioPath)}</div>
      <div class="actions">
        <button disabled={exporting} onclick={() => exportAs("txt")}>导出 TXT</button>
        <button disabled={exporting} onclick={() => exportAs("srt")}>导出 SRT</button>
      </div>
    </div>

    <SpeakerRename speakers={detail.speakers} {onRename} />

    <div class="transcript">
      {#each blocks as b}
        <div class="block">
          <span class="speaker" style="color:{colorOf(b.speaker)}">{b.speaker}</span>
          <span class="text">{b.text}</span>
        </div>
      {/each}
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

  @media (prefers-color-scheme: dark) {
    .view { --line: #2a2a2e; --card: #232327; --fg: #eaeaea; --muted: #8a8a90; }
  }
</style>
