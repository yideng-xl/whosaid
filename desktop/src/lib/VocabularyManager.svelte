<script module lang="ts">
  export function parseAliases(value: string): string[] {
    const result: string[] = [];
    const seen = new Set<string>();
    for (const part of value.split(/[，,、\n]/)) {
      const alias = part.trim();
      const key = alias.toLocaleLowerCase();
      if (alias && !seen.has(key)) {
        seen.add(key);
        result.push(alias);
      }
    }
    return result;
  }
</script>

<script lang="ts">
  import type {
    createApi,
    VocabularyEntry,
    VocabularyInput,
    VocabularyKind,
  } from "./api";
  import Icon from "./Icon.svelte";

  let {
    api,
    onClose,
  }: {
    api: ReturnType<typeof createApi>;
    onClose: () => void;
  } = $props();

  let entries = $state<VocabularyEntry[]>([]);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let kind = $state<VocabularyKind>("person");
  let canonical = $state("");
  let aliasesText = $state("");
  let adding = $state(false);
  let busyId = $state<string | null>(null);
  let editingId = $state<string | null>(null);
  let editKind = $state<VocabularyKind>("person");
  let editCanonical = $state("");
  let editAliasesText = $state("");
  let deleteTarget = $state<VocabularyEntry | null>(null);

  const people = $derived(entries.filter((entry) => entry.kind === "person"));
  const terms = $derived(entries.filter((entry) => entry.kind === "term"));
  const enabledCount = $derived(entries.filter((entry) => entry.enabled).length);

  $effect(() => {
    void load();
  });

  async function load() {
    loading = true;
    error = null;
    try {
      entries = await api.listVocabulary();
    } catch (reason) {
      error = `加载词库失败：${reason}`;
    } finally {
      loading = false;
    }
  }

  function inputOf(
    entryKind: VocabularyKind,
    standard: string,
    aliases: string,
    enabled: boolean,
  ): VocabularyInput {
    return {
      kind: entryKind,
      canonical: standard.trim(),
      aliases: parseAliases(aliases),
      enabled,
    };
  }

  async function add() {
    if (!canonical.trim() || adding) return;
    adding = true;
    error = null;
    try {
      const created = await api.addVocabulary(inputOf(kind, canonical, aliasesText, true));
      entries = [...entries, created];
      canonical = "";
      aliasesText = "";
    } catch (reason) {
      error = `添加失败：${reason}`;
    } finally {
      adding = false;
    }
  }

  function beginEdit(entry: VocabularyEntry) {
    editingId = entry.id;
    editKind = entry.kind;
    editCanonical = entry.canonical;
    editAliasesText = entry.aliases.join("、");
    error = null;
  }

  async function saveEdit(entry: VocabularyEntry) {
    if (!editCanonical.trim() || busyId) return;
    busyId = entry.id;
    error = null;
    try {
      const updated = await api.updateVocabulary(
        entry.id,
        inputOf(editKind, editCanonical, editAliasesText, entry.enabled),
      );
      entries = entries.map((item) => item.id === updated.id ? updated : item);
      editingId = null;
    } catch (reason) {
      error = `保存失败：${reason}`;
    } finally {
      busyId = null;
    }
  }

  async function toggle(entry: VocabularyEntry) {
    if (busyId) return;
    busyId = entry.id;
    error = null;
    try {
      const updated = await api.updateVocabulary(entry.id, {
        kind: entry.kind,
        canonical: entry.canonical,
        aliases: entry.aliases,
        enabled: !entry.enabled,
      });
      entries = entries.map((item) => item.id === updated.id ? updated : item);
    } catch (reason) {
      error = `更新失败：${reason}`;
    } finally {
      busyId = null;
    }
  }

  async function remove() {
    const target = deleteTarget;
    if (!target || busyId) return;
    busyId = target.id;
    error = null;
    try {
      await api.deleteVocabulary(target.id);
      entries = entries.filter((entry) => entry.id !== target.id);
      if (editingId === target.id) editingId = null;
      deleteTarget = null;
    } catch (reason) {
      error = `删除失败：${reason}`;
    } finally {
      busyId = null;
    }
  }
</script>

<section class="manager">
  <header>
    <div>
      <h1>姓名库与专用词库</h1>
      <p>已启用 {enabledCount} 条。新任务开始时会把标准写法交给转写模型，已有稿件不会自动改动。</p>
    </div>
    <button class="icon-button" aria-label="关闭词库" onclick={onClose}>
      <Icon name="close" size={16} />
    </button>
  </header>

  <form class="composer" onsubmit={(event) => { event.preventDefault(); void add(); }}>
    <label>
      <span>类型</span>
      <select bind:value={kind}>
        <option value="person">姓名</option>
        <option value="term">专用词</option>
      </select>
    </label>
    <label class="standard-field">
      <span>标准写法</span>
      <input bind:value={canonical} maxlength="80" placeholder={kind === "person" ? "例如：许磊" : "例如：端到端探测"} />
    </label>
    <label class="aliases-field">
      <span>常见误写或别名（可选）</span>
      <input bind:value={aliasesText} placeholder="用逗号或顿号分隔" />
    </label>
    <button class="primary" type="submit" disabled={adding || !canonical.trim()}>
      {adding ? "添加中…" : "添加"}
    </button>
  </form>

  {#if error}<div class="error" role="alert">{error}</div>{/if}

  {#if loading}
    <div class="empty">正在加载词库…</div>
  {:else if entries.length === 0}
    <div class="empty">词库还是空的。可以先加入会议中经常出现的人名和产品名称。</div>
  {:else}
    {#each [["姓名库", people], ["专用词库", terms]] as group (group[0])}
      {@const title = group[0] as string}
      {@const items = group[1] as VocabularyEntry[]}
      <section class="group">
        <h2>{title}<span>{items.length}</span></h2>
        {#if items.length === 0}
          <div class="group-empty">暂无内容</div>
        {/if}
        {#each items as entry (entry.id)}
          <article class="entry" class:disabled={!entry.enabled}>
            {#if editingId === entry.id}
              <div class="edit-grid">
                <select aria-label="编辑类型" bind:value={editKind}>
                  <option value="person">姓名</option>
                  <option value="term">专用词</option>
                </select>
                <input aria-label="编辑标准写法" bind:value={editCanonical} maxlength="80" />
                <input aria-label="编辑常见误写或别名" bind:value={editAliasesText} placeholder="用逗号或顿号分隔" />
              </div>
              <div class="actions">
                <button class="secondary" onclick={() => (editingId = null)}>取消</button>
                <button class="primary small" disabled={busyId === entry.id || !editCanonical.trim()} onclick={() => void saveEdit(entry)}>保存</button>
              </div>
            {:else}
              <button
                class="switch"
                class:on={entry.enabled}
                role="switch"
                aria-checked={entry.enabled}
                aria-label={`${entry.enabled ? "停用" : "启用"}${entry.canonical}`}
                disabled={busyId !== null}
                onclick={() => void toggle(entry)}
              ><span></span></button>
              <div class="entry-copy">
                <strong>{entry.canonical}</strong>
                {#if entry.aliases.length > 0}
                  <small>误写或别名：{entry.aliases.join("、")}</small>
                {:else}
                  <small>暂无误写或别名</small>
                {/if}
              </div>
              <div class="actions">
                <button class="secondary" onclick={() => beginEdit(entry)}>编辑</button>
                <button class="danger" onclick={() => (deleteTarget = entry)}>删除</button>
              </div>
            {/if}
          </article>
        {/each}
      </section>
    {/each}
  {/if}
</section>

{#if deleteTarget}
  <div class="backdrop" role="presentation">
    <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="delete-vocabulary-title">
      <h2 id="delete-vocabulary-title">删除词库条目</h2>
      <p>确定删除“{deleteTarget.canonical}”吗？删除后不会影响已经生成的稿件。</p>
      <div class="dialog-actions">
        <button class="secondary" disabled={busyId === deleteTarget.id} onclick={() => (deleteTarget = null)}>取消</button>
        <button class="danger solid" disabled={busyId === deleteTarget.id} onclick={() => void remove()}>
          {busyId === deleteTarget.id ? "删除中…" : "确认删除"}
        </button>
      </div>
    </div>
  </div>
{/if}

<style>
  .manager { max-width: 920px; margin: 0 auto; padding: var(--space-6); color: var(--fg); }
  header { display: flex; justify-content: space-between; align-items: flex-start; gap: var(--space-4); margin-bottom: var(--space-5); }
  h1 { margin: 0 0 6px; font-size: 24px; }
  header p { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.5; }
  .icon-button { border: 1px solid var(--hairline); border-radius: var(--radius-btn); background: var(--card); color: var(--fg); padding: 7px; cursor: pointer; }
  .composer { display: grid; grid-template-columns: 110px minmax(180px, 1fr) minmax(220px, 1.3fr) auto; align-items: end; gap: var(--space-2); padding: var(--space-4); border: 1px solid var(--hairline); border-radius: var(--radius-card); background: var(--card); }
  label { display: flex; flex-direction: column; gap: 6px; }
  label span { color: var(--muted); font-size: 12px; }
  input, select { box-sizing: border-box; width: 100%; height: 34px; border: 1px solid var(--hairline); border-radius: var(--radius-btn); background: var(--bg); color: var(--fg); padding: 0 10px; font: inherit; font-size: 13px; }
  input:focus, select:focus { outline: 2px solid var(--focus); outline-offset: 1px; }
  button { font: inherit; }
  .primary, .secondary, .danger { min-height: 32px; border-radius: var(--radius-btn); padding: 0 12px; cursor: pointer; }
  .primary { border: 1px solid var(--accent); background: var(--accent); color: #fff; font-weight: 600; }
  .primary.small { min-width: 64px; }
  .secondary { border: 1px solid var(--hairline); background: var(--card); color: var(--fg); }
  .danger { border: 1px solid color-mix(in srgb, var(--danger) 40%, var(--hairline)); background: transparent; color: var(--danger); }
  .danger.solid { background: var(--danger); color: #fff; border-color: var(--danger); }
  button:disabled { opacity: .5; cursor: default; }
  .error { margin-top: var(--space-3); padding: 10px 12px; border-radius: var(--radius-btn); background: color-mix(in srgb, var(--danger) 10%, transparent); color: var(--danger); font-size: 13px; }
  .empty { margin-top: var(--space-5); padding: 36px; border: 1px dashed var(--hairline); border-radius: var(--radius-card); text-align: center; color: var(--muted); }
  .group { margin-top: var(--space-5); }
  .group h2 { display: flex; align-items: center; gap: 7px; margin: 0 0 var(--space-2); font-size: 15px; }
  .group h2 span { min-width: 18px; border-radius: 10px; background: color-mix(in srgb, var(--accent) 12%, transparent); color: var(--accent); text-align: center; font-size: 11px; line-height: 18px; }
  .group-empty { padding: var(--space-3); color: var(--muted); font-size: 13px; }
  .entry { display: flex; align-items: center; gap: var(--space-3); min-height: 58px; padding: 10px 12px; border: 1px solid var(--hairline); border-bottom-width: 0; background: var(--card); }
  .entry:first-of-type { border-radius: var(--radius-card) var(--radius-card) 0 0; }
  .entry:last-child { border-bottom-width: 1px; border-radius: 0 0 var(--radius-card) var(--radius-card); }
  .entry.disabled .entry-copy { opacity: .5; }
  .entry-copy { display: flex; min-width: 0; flex: 1; flex-direction: column; gap: 4px; }
  .entry-copy strong { font-size: 14px; }
  .entry-copy small { overflow: hidden; color: var(--muted); text-overflow: ellipsis; white-space: nowrap; }
  .actions { display: flex; gap: 6px; margin-left: auto; }
  .actions button { min-height: 28px; padding: 0 9px; font-size: 12px; }
  .switch { position: relative; width: 34px; height: 20px; flex: 0 0 auto; border: 0; border-radius: 11px; background: var(--hairline); padding: 0; cursor: pointer; }
  .switch span { position: absolute; top: 2px; left: 2px; width: 16px; height: 16px; border-radius: 50%; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,.22); transition: transform .12s; }
  .switch.on { background: var(--accent); }
  .switch.on span { transform: translateX(14px); }
  .edit-grid { display: grid; min-width: 0; flex: 1; grid-template-columns: 110px minmax(150px, .8fr) minmax(220px, 1.2fr); gap: var(--space-2); }
  .backdrop { position: fixed; inset: 0; z-index: 20; display: grid; place-items: center; background: rgba(0,0,0,.38); }
  .dialog { width: min(400px, calc(100vw - 40px)); padding: var(--space-5); border: 1px solid var(--hairline); border-radius: var(--radius-modal); background: var(--card); box-shadow: 0 18px 60px rgba(0,0,0,.3); }
  .dialog h2 { margin: 0 0 var(--space-2); font-size: 17px; }
  .dialog p { margin: 0; color: var(--muted); line-height: 1.6; }
  .dialog-actions { display: flex; justify-content: flex-end; gap: var(--space-2); margin-top: var(--space-5); }
  @media (max-width: 850px) {
    .composer { grid-template-columns: 100px 1fr; }
    .aliases-field { grid-column: 1 / -1; }
    .edit-grid { grid-template-columns: 100px 1fr; }
    .edit-grid input:last-child { grid-column: 1 / -1; }
  }
</style>
