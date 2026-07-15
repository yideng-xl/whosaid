<script lang="ts">
  import type { createApi, ModelInfo } from "./api";

  let {
    api,
    onClose,
  }: {
    api: ReturnType<typeof createApi>;
    onClose: () => void;
  } = $props();

  let models = $state<ModelInfo[]>([]);
  let loadError = $state<string | null>(null);
  let busyId = $state<string | null>(null); // 正在下载/切换的模型 id

  const KIND_LABEL: Record<string, string> = {
    transcribe: "语音转写",
    diarize: "说话人分离",
  };

  // 按 kind 分组，保持后端返回顺序
  const groups = $derived.by(() => {
    const m = new Map<string, ModelInfo[]>();
    for (const it of models) {
      if (!m.has(it.kind)) m.set(it.kind, []);
      m.get(it.kind)!.push(it);
    }
    return [...m.entries()];
  });

  $effect(() => {
    void load();
  });

  async function load() {
    loadError = null;
    try {
      models = await api.listModels();
    } catch (e) {
      loadError = `加载模型列表失败：${e}`;
    }
  }

  async function download(id: string) {
    busyId = id;
    try {
      await api.downloadModel(id); // 同步阻塞：返回即下载完成
      await load();
    } catch (e) {
      loadError = `下载失败：${e}`;
    } finally {
      busyId = null;
    }
  }

  async function setActive(id: string) {
    busyId = id;
    try {
      await api.setActive(id);
      await load();
    } catch (e) {
      loadError = `切换失败：${e}`;
    } finally {
      busyId = null;
    }
  }

  function sizeText(mb: number): string {
    return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`;
  }
</script>

<div class="mm">
  <div class="head">
    <div class="title">模型管理</div>
    <button class="back" onclick={onClose}>← 返回</button>
  </div>

  {#if loadError}
    <div class="err">{loadError}</div>
  {/if}

  {#each groups as [kind, items] (kind)}
    <div class="group">
      <div class="group-title">{KIND_LABEL[kind] ?? kind}</div>
      {#each items as m (m.id)}
        <div class="model" class:active={m.active}>
          <div class="info">
            <span class="mname">{m.display_name}</span>
            <span class="msize">{sizeText(m.size_mb)}</span>
          </div>
          <div class="tags">
            {#if m.active}<span class="tag on">● 当前</span>{/if}
            {#if m.downloaded}
              <span class="tag ok">✓ 已下载</span>
            {:else}
              <span class="tag no">未下载</span>
            {/if}
          </div>
          <div class="ops">
            {#if !m.downloaded}
              <button disabled={busyId === m.id} onclick={() => download(m.id)}>
                {busyId === m.id ? "下载中…" : "下载"}
              </button>
            {:else if !m.active}
              <button disabled={busyId === m.id} onclick={() => setActive(m.id)}>
                {busyId === m.id ? "切换中…" : "设为当前"}
              </button>
            {/if}
          </div>
        </div>
      {/each}
    </div>
  {/each}

  <p class="note">下载较大模型时会一直转圈直到完成（服务端同步下载），请耐心等待。切换当前模型对下一个任务生效。</p>
</div>

<style>
  .mm { max-width: 720px; }
  .head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 18px;
  }
  .title { font-size: 18px; font-weight: 600; color: var(--fg, #1a1a1a); }
  .back {
    background: transparent;
    border: 1px solid var(--line, #d8d8dc);
    border-radius: 6px;
    padding: 5px 12px;
    font: inherit;
    font-size: 13px;
    color: var(--muted, #6a6a70);
    cursor: pointer;
  }
  .back:hover { border-color: var(--accent, #3b7ddd); color: var(--accent, #3b7ddd); }
  .err { color: #cf3b3b; font-size: 13px; margin-bottom: 12px; }
  .group { margin-bottom: 22px; }
  .group-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--muted, #8a8a90);
    margin-bottom: 10px;
  }
  .model {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 14px;
    border: 1px solid var(--line, #e8e8ec);
    border-radius: 8px;
    margin-bottom: 8px;
    background: var(--card, #fff);
  }
  .model.active { border-color: var(--accent, #3b7ddd); }
  .info { flex: 1; display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .mname { font-size: 14px; color: var(--fg, #1a1a1a); }
  .msize { font-size: 12px; color: var(--muted, #9a9aa0); }
  .tags { display: flex; gap: 6px; flex-shrink: 0; }
  .tag { font-size: 11px; padding: 1px 8px; border-radius: 999px; white-space: nowrap; }
  .tag.on { background: #e5efff; color: #2f6fd0; }
  .tag.ok { background: #e3f6e8; color: #2c8a4b; }
  .tag.no { background: #eee; color: #888; }
  .ops { flex-shrink: 0; min-width: 84px; text-align: right; }
  .ops button {
    padding: 5px 12px;
    border: 1px solid var(--accent, #3b7ddd);
    background: var(--accent, #3b7ddd);
    color: #fff;
    border-radius: 6px;
    font: inherit;
    font-size: 12px;
    cursor: pointer;
  }
  .ops button:disabled { opacity: 0.5; cursor: default; }
  .note { font-size: 12px; color: var(--muted, #9a9aa0); margin-top: 8px; line-height: 1.6; }

  /* 深色主题：由 <html data-theme="dark"> 驱动，不再依赖媒体查询；边框调亮、卡片压深以拉开明暗对比 */
  :global(:root[data-theme="dark"]) .mm { --line: #3a3a40; --card: #1f1f23; --fg: #eaeaea; --muted: #8a8a90; }
  :global(:root[data-theme="dark"]) .tag.on { background: #1c3a5e; color: #7fb0ff; }
  :global(:root[data-theme="dark"]) .tag.ok { background: #1e3d28; color: #7fd39a; }
  :global(:root[data-theme="dark"]) .tag.no { background: #333; color: #aaa; }
</style>
