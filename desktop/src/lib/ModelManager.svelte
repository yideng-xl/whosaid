<script lang="ts">
  import type { createApi, ModelInfo } from "./api";
  import Icon from "./Icon.svelte";

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
    <button class="close-btn" aria-label="关闭模型管理" title="返回" onclick={onClose}>
      <Icon name="close" size={14} />
    </button>
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
            {#if m.active}
              <span class="tag on"><span class="dot"></span>当前</span>
            {/if}
            {#if m.downloaded}
              <span class="tag ok"><Icon name="check" size={10} />已下载</span>
            {:else}
              <span class="tag no">未下载</span>
            {/if}
          </div>
          <div class="ops">
            {#if !m.downloaded}
              <button class="btn-primary" disabled={busyId === m.id} onclick={() => download(m.id)}>
                {busyId === m.id ? "下载中…" : "下载"}
              </button>
            {:else if !m.active}
              <button class="btn-secondary" disabled={busyId === m.id} onclick={() => setActive(m.id)}>
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
  .title { font-size: 18px; font-weight: 600; color: var(--fg); }

  /* 关闭按钮：圆形描边图标钮，与 Sidebar 主题切换钮同一套触控尺寸(28px)，
     hover 描边/文字转 accent 色，按压 scale(0.97)，聚焦环用 --focus。 */
  .close-btn {
    flex-shrink: 0;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    background: transparent;
    border: 1px solid var(--hairline);
    border-radius: 50%;
    color: var(--muted);
    cursor: pointer;
    transition: border-color 0.15s ease, color 0.15s ease, transform 0.12s ease;
  }
  .close-btn:hover { border-color: var(--accent); color: var(--accent); }
  .close-btn:active { transform: scale(0.97); }
  .close-btn:focus-visible { outline: 2px solid var(--focus); outline-offset: 1px; }

  .err { color: var(--danger); font-size: 13px; margin-bottom: 12px; }
  .group { margin-bottom: 22px; }
  .group-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--muted);
    margin-bottom: 10px;
  }

  /* 模型行卡片：发丝描边 + 卡片底色，hover 描边微微透出 accent，
     当前项(active)描边实心 accent + 淡 accent 底，替代原先纯边框高亮。 */
  .model {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 14px;
    border: 1px solid var(--hairline);
    border-radius: var(--radius-card);
    margin-bottom: 8px;
    background: var(--card);
    transition: border-color 0.15s ease, background 0.15s ease;
  }
  .model:hover:not(.active) {
    border-color: color-mix(in srgb, var(--accent) 40%, var(--hairline));
  }
  .model.active {
    border-color: var(--accent);
    background: color-mix(in srgb, var(--accent) 6%, var(--card));
  }
  .info { flex: 1; display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .mname { font-size: 14px; color: var(--fg); }
  .msize { font-size: 12px; color: var(--muted); }
  .tags { display: flex; gap: 6px; flex-shrink: 0; }
  .tag {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    font-size: 11px;
    padding: 1px 8px;
    border-radius: 999px;
    white-space: nowrap;
  }
  /* 徽标底色沿用 Sidebar 的低饱和 color-mix 公式，无需单独维护深色覆盖 */
  .tag.on { background: color-mix(in srgb, var(--accent) 16%, transparent); color: var(--accent); }
  .tag.ok { background: color-mix(in srgb, #2c8a4b 16%, transparent); color: #2c8a4b; }
  .tag.no { background: color-mix(in srgb, var(--muted) 18%, transparent); color: var(--muted); }
  .tag .dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: currentColor;
  }
  .ops { flex-shrink: 0; min-width: 84px; text-align: right; }
  .ops button {
    padding: 5px 12px;
    border-radius: var(--radius-btn);
    font: inherit;
    font-size: 12px;
    cursor: pointer;
    border: 1px solid transparent;
    transition: transform 0.12s ease, opacity 0.15s ease, background 0.15s ease, color 0.15s ease;
  }
  .ops button:disabled { opacity: 0.5; cursor: default; }
  .ops button:active:not(:disabled) { transform: scale(0.97); }
  .ops button:focus-visible { outline: 2px solid var(--focus); outline-offset: 1px; }
  /* 主按钮：下载（首次获取，实心 accent 白字） */
  .btn-primary {
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
  }
  .btn-primary:hover:not(:disabled) { opacity: 0.9; }
  /* 次按钮：设为当前（已下载后的切换动作，低强调描边） */
  .btn-secondary {
    background: transparent;
    border-color: var(--accent);
    color: var(--accent);
  }
  .btn-secondary:hover:not(:disabled) {
    background: color-mix(in srgb, var(--accent) 10%, transparent);
  }
  .note { font-size: 12px; color: var(--muted); margin-top: 8px; line-height: 1.6; }
</style>
