<script module lang="ts">
  export function parseTerms(value: string): string[] {
    const result: string[] = [];
    const seen = new Set<string>();
    for (const part of value.split(/[，,、\n]/)) {
      const term = part.trim();
      const key = term.toLocaleLowerCase();
      if (term && !seen.has(key)) {
        seen.add(key);
        result.push(term);
      }
    }
    return result;
  }
</script>

<script lang="ts">
  import type {
    createApi,
    VocabularyLibrary,
    VocabularyScope,
  } from "./api";
  import Icon from "./Icon.svelte";

  let { api, onClose }: {
    api: ReturnType<typeof createApi>;
    onClose: () => void;
  } = $props();

  let libraries = $state<VocabularyLibrary[]>([]);
  let loading = $state(true);
  let saving = $state(false);
  let deleting = $state(false);
  let error = $state<string | null>(null);
  let editingId = $state<string | null>(null);
  let editorOpen = $state(false);
  let name = $state("");
  let scope = $state<VocabularyScope>("specialized");
  let termsText = $state("");
  let deleteTarget = $state<VocabularyLibrary | null>(null);

  const termCount = $derived(parseTerms(termsText).length);
  const generalLibraries = $derived(libraries.filter((item) => item.scope === "general"));
  const specializedLibraries = $derived(libraries.filter((item) => item.scope === "specialized"));

  $effect(() => { void load(); });

  async function load() {
    loading = true;
    error = null;
    try {
      libraries = await api.listVocabulary();
    } catch (reason) {
      error = `加载词库失败：${reason}`;
    } finally {
      loading = false;
    }
  }

  function openNew() {
    editingId = null;
    name = "";
    scope = "specialized";
    termsText = "";
    error = null;
    editorOpen = true;
  }

  function openEdit(library: VocabularyLibrary) {
    editingId = library.id;
    name = library.name;
    scope = library.scope;
    termsText = library.terms.join("、");
    error = null;
    editorOpen = true;
  }

  function closeEditor() {
    if (saving) return;
    editorOpen = false;
    editingId = null;
  }

  async function save() {
    if (saving) return;
    const input = { name: name.trim(), scope, terms: parseTerms(termsText) };
    if (!input.name) {
      error = "请填写词库名称";
      return;
    }
    saving = true;
    error = null;
    try {
      const saved = editingId
        ? await api.updateVocabulary(editingId, input)
        : await api.addVocabulary(input);
      libraries = editingId
        ? libraries.map((item) => item.id === saved.id ? saved : item)
        : [...libraries, saved];
      editorOpen = false;
      editingId = null;
    } catch (reason) {
      error = `保存失败：${reason}`;
    } finally {
      saving = false;
    }
  }

  async function confirmDelete() {
    if (!deleteTarget || deleting) return;
    deleting = true;
    error = null;
    const target = deleteTarget;
    try {
      await api.deleteVocabulary(target.id);
      libraries = libraries.filter((item) => item.id !== target.id);
      deleteTarget = null;
      if (editingId === target.id) closeEditor();
    } catch (reason) {
      error = `删除失败：${reason}`;
    } finally {
      deleting = false;
    }
  }
</script>

<section class="manager">
  <header>
    <div>
      <h1>词库</h1>
      <p>按主题维护多个词库。转写前可按本次会议选择，避免无关词条干扰识别。</p>
    </div>
    <div class="header-actions">
      <button class="primary" onclick={openNew}>新建词库</button>
      <button class="icon-button" aria-label="关闭词库" onclick={onClose}>
        <Icon name="close" size={16} />
      </button>
    </div>
  </header>

  {#if error}<div class="error" role="alert">{error}</div>{/if}

  {#if loading}
    <div class="loading">正在加载词库…</div>
  {:else if libraries.length === 0}
    <div class="empty">
      <h2>还没有词库</h2>
      <p>可以先建一个通用「姓名」词库，再按产品或会议主题建立专用词库。</p>
      <button class="primary" onclick={openNew}>新建第一个词库</button>
    </div>
  {:else}
    <div class="groups">
      <section class="group" aria-labelledby="general-title">
        <div class="group-heading">
          <div>
            <h2 id="general-title">通用词库</h2>
            <p>每次新建转写任务时默认选中，可在提交前取消。</p>
          </div>
          <span>{generalLibraries.length} 个</span>
        </div>
        <div class="library-grid">
          {#each generalLibraries as library (library.id)}
            <article class="library-card">
              <button class="card-main" aria-label={`编辑词库 ${library.name}`} onclick={() => openEdit(library)}>
                <span class="library-title">{library.name}</span>
                <span class="library-meta">通用 · {library.terms.length} 个词</span>
                <span class="library-preview">{library.terms.slice(0, 8).join("、") || "暂无词条"}</span>
              </button>
              <button class="danger-link" aria-label={`删除词库 ${library.name}`} onclick={() => (deleteTarget = library)}>删除</button>
            </article>
          {/each}
          {#if generalLibraries.length === 0}<p class="group-empty">暂无通用词库</p>{/if}
        </div>
      </section>

      <section class="group" aria-labelledby="specialized-title">
        <div class="group-heading">
          <div>
            <h2 id="specialized-title">专用词库</h2>
            <p>转写任务默认不选，按本次会议涉及的产品或主题选择。</p>
          </div>
          <span>{specializedLibraries.length} 个</span>
        </div>
        <div class="library-grid">
          {#each specializedLibraries as library (library.id)}
            <article class="library-card">
              <button class="card-main" aria-label={`编辑词库 ${library.name}`} onclick={() => openEdit(library)}>
                <span class="library-title">{library.name}</span>
                <span class="library-meta">专用 · {library.terms.length} 个词</span>
                <span class="library-preview">{library.terms.slice(0, 8).join("、") || "暂无词条"}</span>
              </button>
              <button class="danger-link" aria-label={`删除词库 ${library.name}`} onclick={() => (deleteTarget = library)}>删除</button>
            </article>
          {/each}
          {#if specializedLibraries.length === 0}<p class="group-empty">暂无专用词库</p>{/if}
        </div>
      </section>
    </div>
  {/if}
</section>

{#if editorOpen}
  <div class="modal-backdrop" role="presentation">
    <div class="modal editor" role="dialog" aria-modal="true" aria-labelledby="library-editor-title">
      <div class="modal-heading">
        <h2 id="library-editor-title">{editingId ? "编辑词库" : "新建词库"}</h2>
        <button class="icon-button" aria-label="关闭编辑" disabled={saving} onclick={closeEditor}><Icon name="close" size={16} /></button>
      </div>
      <label>
        <span class="label-title">词库名称</span>
        <input bind:value={name} maxlength="80" placeholder="例如：姓名、产品甲、产品乙" />
      </label>
      <fieldset>
        <legend>词库类型</legend>
        <label class="scope-option">
          <input type="radio" bind:group={scope} value="general" />
          <span><b>通用词库</b><small>每次转写默认选中，例如姓名、组织名称</small></span>
        </label>
        <label class="scope-option">
          <input type="radio" bind:group={scope} value="specialized" />
          <span><b>专用词库</b><small>每次转写默认不选，例如产品或项目词库</small></span>
        </label>
      </fieldset>
      <label>
        <span class="label-title">词库内容 <small>{termCount} 个词</small></span>
        <span class="field-help">每个词之间用逗号、顿号或换行隔开。</span>
        <textarea bind:value={termsText} rows="9" placeholder="例如：示例探测、示例词46、示例词190"></textarea>
      </label>
      <div class="modal-actions">
        <button class="secondary" disabled={saving} onclick={closeEditor}>取消</button>
        <button class="primary" disabled={saving} aria-busy={saving} onclick={() => void save()}>{saving ? "保存中…" : "保存"}</button>
      </div>
    </div>
  </div>
{/if}

{#if deleteTarget}
  <div class="modal-backdrop" role="presentation">
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="delete-library-title">
      <h2 id="delete-library-title">删除词库</h2>
      <p>确定删除「{deleteTarget.name}」？词库内容将永久删除，但不会影响已经提交的任务。</p>
      <div class="modal-actions">
        <button class="secondary" disabled={deleting} onclick={() => (deleteTarget = null)}>取消</button>
        <button class="danger" disabled={deleting} aria-busy={deleting} onclick={() => void confirmDelete()}>{deleting ? "删除中…" : "删除"}</button>
      </div>
    </div>
  </div>
{/if}

<style>
  .manager { box-sizing: border-box; width: min(980px, 100%); margin: 0 auto; padding: var(--space-6); color: var(--fg); }
  header, .header-actions, .group-heading, .modal-heading, .modal-actions { display: flex; align-items: center; }
  header, .group-heading, .modal-heading { justify-content: space-between; }
  header { align-items: flex-start; gap: var(--space-4); margin-bottom: var(--space-5); }
  h1, h2, p { margin-top: 0; }
  h1 { margin-bottom: 6px; font-size: 24px; }
  h2 { margin-bottom: 5px; font-size: 16px; }
  header p, .group-heading p, .empty p, .modal p { margin-bottom: 0; color: var(--muted); font-size: 13px; line-height: 1.55; }
  .header-actions { gap: 8px; }
  button { min-height: 36px; font: inherit; cursor: pointer; }
  button:focus-visible, input:focus-visible, textarea:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
  button:disabled { cursor: default; opacity: .55; }
  .icon-button { min-width: 36px; border: 1px solid var(--hairline); border-radius: var(--radius-btn); background: var(--card); color: var(--fg); }
  .primary, .secondary, .danger { border-radius: var(--radius-btn); padding: 0 16px; font-weight: 600; }
  .primary { border: 1px solid var(--accent); background: var(--accent); color: white; }
  .secondary { border: 1px solid var(--hairline); background: var(--card); color: var(--fg); }
  .danger { border: 1px solid var(--danger); background: var(--danger); color: white; }
  .groups { display: grid; gap: var(--space-5); }
  .group { padding: var(--space-4); border: 1px solid var(--hairline); border-radius: var(--radius-card); background: var(--card); }
  .group-heading { align-items: flex-start; padding-bottom: var(--space-3); border-bottom: 1px solid var(--hairline); }
  .group-heading > span { color: var(--muted); font-size: 12px; }
  .library-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; padding-top: var(--space-3); }
  .library-card { display: flex; align-items: stretch; min-width: 0; border: 1px solid var(--hairline); border-radius: var(--radius-btn); background: var(--bg); overflow: hidden; }
  .card-main { display: grid; flex: 1; min-width: 0; gap: 4px; padding: 12px; border: 0; background: transparent; color: var(--fg); text-align: left; }
  .card-main:hover { background: color-mix(in srgb, var(--accent) 7%, transparent); }
  .library-title { font-weight: 650; }
  .library-meta { color: var(--accent); font-size: 12px; }
  .library-preview { overflow: hidden; color: var(--muted); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
  .danger-link { align-self: center; min-width: 52px; border: 0; border-left: 1px solid var(--hairline); background: transparent; color: var(--danger); font-size: 12px; }
  .group-empty { grid-column: 1 / -1; padding: 14px 0 4px; text-align: center; }
  .empty, .loading { padding: 56px 24px; border: 1px dashed var(--hairline); border-radius: var(--radius-card); text-align: center; color: var(--muted); }
  .empty .primary { margin-top: var(--space-4); }
  .error { margin-bottom: var(--space-3); padding: 10px 12px; border-radius: var(--radius-btn); background: color-mix(in srgb, var(--danger) 10%, transparent); color: var(--danger); font-size: 13px; }
  .modal-backdrop { position: fixed; inset: 0; z-index: 30; display: flex; align-items: center; justify-content: center; padding: 24px; background: rgba(0, 0, 0, .48); }
  .modal { box-sizing: border-box; width: min(460px, 100%); padding: var(--space-5); border: 1px solid var(--hairline); border-radius: var(--radius-card); background: var(--card); color: var(--fg); box-shadow: 0 18px 48px rgba(0, 0, 0, .28); }
  .editor { width: min(620px, 100%); max-height: calc(100vh - 48px); overflow: auto; }
  .modal-heading { margin-bottom: var(--space-4); }
  .modal-heading h2 { margin: 0; font-size: 20px; }
  .editor > label { display: grid; gap: 6px; margin-bottom: var(--space-4); }
  .label-title, legend { font-size: 14px; font-weight: 650; }
  .label-title small { color: var(--accent); font-weight: 500; }
  .field-help { color: var(--muted); font-size: 12px; }
  input:not([type]), textarea { box-sizing: border-box; width: 100%; border: 1px solid var(--hairline); border-radius: var(--radius-btn); background: var(--bg); color: var(--fg); padding: 10px 12px; font: inherit; }
  textarea { min-height: 150px; resize: vertical; line-height: 1.65; }
  fieldset { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 0 0 var(--space-4); padding: 0; border: 0; }
  legend { margin-bottom: 8px; }
  .scope-option { display: flex; gap: 9px; padding: 11px; border: 1px solid var(--hairline); border-radius: var(--radius-btn); background: var(--bg); }
  .scope-option input { margin-top: 3px; }
  .scope-option span { display: grid; gap: 3px; }
  .scope-option small { color: var(--muted); line-height: 1.4; }
  .modal-actions { justify-content: flex-end; gap: 8px; margin-top: var(--space-5); }
  @media (max-width: 720px) { .library-grid, fieldset { grid-template-columns: 1fr; } .manager { padding: var(--space-4); } }
</style>
