<script lang="ts">
  import type { createApi, ModelInfo } from "./api";
  import Icon from "./Icon.svelte";
  import { openUrl } from "@tauri-apps/plugin-opener";

  let {
    api,
    onClose,
  }: {
    api: ReturnType<typeof createApi>;
    onClose: () => void;
  } = $props();

  let models = $state<ModelInfo[]>([]);
  let loadError = $state<string | null>(null);
  let busyId = $state<string | null>(null); // 正在下载/切换/删除的模型 id
  // 待确认删除的模型（点删除先弹二次确认，避免误删需要重新下载）
  let deleteTarget = $state<ModelInfo | null>(null);

  let hfToken = $state("");
  let hfEndpoint = $state("");
  let hfSaving = $state(false);
  let hfSaved = $state(false);
  let hfError = $state<string | null>(null);

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
    try {
      const s = await api.getHfSettings();
      hfToken = s.hf_token ?? "";
      hfEndpoint = s.hf_endpoint ?? "";
    } catch (e) {
      hfError = `加载 HF 设置失败：${e}`;
    }
  }

  async function saveHfSettings() {
    hfSaving = true;
    hfError = null;
    hfSaved = false;
    try {
      await api.setHfSettings({ hf_token: hfToken || null, hf_endpoint: hfEndpoint || null });
      hfSaved = true;
    } catch (e) {
      hfError = `保存失败：${e}`;
    } finally {
      hfSaving = false;
    }
  }

  function openTutorial() {
    void openUrl("https://yideng-xl.github.io/whosaid/#hf-token");
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

  async function confirmDelete() {
    if (!deleteTarget) return;
    const id = deleteTarget.id;
    deleteTarget = null;
    busyId = id;
    try {
      await api.deleteModel(id);
      await load();
    } catch (e) {
      loadError = `删除失败：${e}`;
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
    <button class="close-btn" aria-label="关闭模型管理" title="关闭模型管理" onclick={onClose}>
      <Icon name="close" size={14} />
    </button>
  </div>

  {#if loadError}
    <div class="err">{loadError}</div>
  {/if}

  <div class="hf-section">
    <div class="group-title">HuggingFace 访问设置</div>
    <p class="hf-note">
      下载 pyannote 说话人分离模型（门控模型）需要你自己的 HuggingFace 访问令牌，并在
      HuggingFace 网站同意对应模型的使用条款。
      <button class="link-btn" onclick={openTutorial}>查看教程</button>
    </p>
    {#if hfError}
      <div class="err">{hfError}</div>
    {/if}
    <label class="hf-field">
      <span>HF 访问令牌</span>
      <input type="password" bind:value={hfToken} placeholder="hf_xxxxxxxxxxxx" autocomplete="off" />
    </label>
    <label class="hf-field">
      <span>镜像地址（可选）</span>
      <input type="text" bind:value={hfEndpoint} placeholder="留空使用官方 huggingface.co，如 https://hf-mirror.com" />
    </label>
    <button class="btn-primary hf-save" disabled={hfSaving} onclick={saveHfSettings}>
      {hfSaving ? "保存中…" : hfSaved ? "已保存" : "保存"}
    </button>
  </div>

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
            {:else}
              {#if !m.active}
                <button class="btn-secondary" disabled={busyId === m.id} onclick={() => setActive(m.id)}>
                  {busyId === m.id ? "切换中…" : "设为当前"}
                </button>
              {/if}
              <button
                class="btn-delete"
                disabled={busyId === m.id}
                onclick={() => (deleteTarget = m)}
              >
                删除
              </button>
            {/if}
          </div>
        </div>
      {/each}
    </div>
  {/each}

  <p class="note">下载较大模型时会一直转圈直到完成（服务端同步下载），请耐心等待。切换当前模型对下一个任务生效。</p>

  {#if deleteTarget}
    <div class="modal-backdrop" role="presentation">
      <div class="modal" role="dialog" aria-modal="true">
        <div class="modal-title">删除模型</div>
        <p class="modal-body">
          确定删除「{deleteTarget.display_name}」？<br />
          删除后<b>需重新下载才能使用</b>。
          {#if deleteTarget.active}
            <br />这是当前启用的模型，删除后对应任务将无法使用它转写/分人，请留意。
          {/if}
        </p>
        <div class="modal-actions">
          <button class="btn-cancel" onclick={() => (deleteTarget = null)}>取消</button>
          <button class="btn-danger" onclick={confirmDelete}>删除</button>
        </div>
      </div>
    </div>
  {/if}
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

  .err { color: var(--danger); font-size: 13px; margin-bottom: var(--space-3); }
  .hf-section {
    padding: 14px;
    border: 1px solid var(--hairline);
    border-radius: var(--radius-card);
    background: var(--card);
    margin-bottom: 22px;
  }
  .hf-note { font-size: 12px; color: var(--muted); line-height: 1.6; margin: 4px 0 12px; }
  .link-btn {
    background: none;
    border: none;
    padding: 0;
    color: var(--accent);
    cursor: pointer;
    font: inherit;
    text-decoration: underline;
  }
  .hf-field {
    display: flex;
    flex-direction: column;
    gap: 4px;
    margin-bottom: 10px;
    font-size: 12px;
    color: var(--fg);
  }
  .hf-field input {
    padding: 6px 10px;
    border: 1px solid var(--hairline);
    border-radius: var(--radius-btn);
    background: var(--bg);
    color: var(--fg);
    font: inherit;
    font-size: 13px;
  }
  .hf-field input:focus-visible { outline: 2px solid var(--focus); outline-offset: 1px; }
  .hf-save { margin-top: 2px; }
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
    gap: var(--space-3);
    padding: 10px 14px;
    border: 1px solid var(--hairline);
    border-radius: var(--radius-card);
    margin-bottom: var(--space-2);
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
    padding: 1px var(--space-2);
    border-radius: 999px;
    white-space: nowrap;
  }
  /* 徽标底色沿用 Sidebar 的低饱和 color-mix 公式，无需单独维护深色覆盖。
     文字色在底色基础上，再与 --fg 做 55% 混合：浅色主题 --fg 近黑把文字拉深，
     深色主题 --fg 近白把文字拉亮，同一份表达式两个主题都能补足对比度，
     同时仍保留 45% 原色相，不会变成刺眼纯色。实测（叠加 .model.active 卡片底、
     即最差场景）：tag.on 浅 7.05:1/深 5.26:1，tag.ok 浅 6.22:1/深 5.16:1，
     tag.no（仅出现在非当前卡片）浅 6.50:1/深 6.01:1，均 ≥4.5:1。 */
  .tag.on { background: color-mix(in srgb, var(--accent) 16%, transparent); color: color-mix(in srgb, var(--accent) 55%, var(--fg)); }
  .tag.ok { background: color-mix(in srgb, #2c8a4b 16%, transparent); color: color-mix(in srgb, #2c8a4b 55%, var(--fg)); }
  .tag.no { background: color-mix(in srgb, var(--muted) 18%, transparent); color: color-mix(in srgb, var(--muted) 55%, var(--fg)); }
  .tag .dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: currentColor;
  }
  .ops { flex-shrink: 0; min-width: 84px; display: flex; align-items: center; justify-content: flex-end; gap: 6px; }
  .ops button {
    padding: 5px var(--space-3);
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
  /* 次按钮：设为当前（已下载后的切换动作，低强调描边）。
     文字改用 --fg（正文近黑/近白高对比色）而非 --accent：深色主题下
     accent(#0a84ff) on card(#2a2a2c) 实测仅 3.93:1 不达标；--fg on --card
     浅色 ≈17.05:1、深色 ≈13.15:1，两主题均远超 4.5:1。accent 保留在描边上
     作低强调标识，符合 macOS bordered 次按钮的惯用样式。 */
  .btn-secondary {
    background: transparent;
    border-color: var(--accent);
    color: var(--fg);
  }
  .btn-secondary:hover:not(:disabled) {
    background: color-mix(in srgb, var(--accent) 10%, transparent);
  }
  /* 删除按钮：低强调描边 + --danger 文字/描边，hover 才转实心，避免列表默认态过于刺眼 */
  .btn-delete {
    background: transparent;
    border-color: var(--danger);
    color: var(--danger);
  }
  .btn-delete:hover:not(:disabled) {
    background: color-mix(in srgb, var(--danger) 10%, transparent);
  }
  .note { font-size: 12px; color: var(--muted); margin-top: var(--space-2); line-height: 1.6; }

  /* 删除确认弹窗：与 +page.svelte 的"删除任务"确认弹窗同一套风格（遮罩 40% 黑 + 卡片 +
     圆角 + 柔和阴影），颜色全部走全局 token，双主题由 :root[data-theme] 统一驱动。 */
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
