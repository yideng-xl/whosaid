<script module lang="ts">
  import type { VocabularyLibrary } from "./api";

  export function defaultVocabularySelection(
    libraries: VocabularyLibrary[],
    initialSelectedIds?: string[],
  ): string[] {
    if (initialSelectedIds !== undefined) {
      const known = new Set(libraries.map((item) => item.id));
      return [...new Set(initialSelectedIds)].filter((id) => known.has(id));
    }
    return libraries.filter((item) => item.scope === "general").map((item) => item.id);
  }
</script>

<script lang="ts">
  let { libraries, audioName, initialSelectedIds, onConfirm, onCancel }: {
    libraries: VocabularyLibrary[];
    audioName: string;
    initialSelectedIds?: string[];
    onConfirm: (libraryIds: string[]) => void | Promise<void>;
    onCancel: () => void;
  } = $props();

  let selected = $state<string[]>([]);
  let selectionInitialized = false;
  let submitting = $state(false);
  let error = $state<string | null>(null);
  const general = $derived(libraries.filter((item) => item.scope === "general"));
  const specialized = $derived(libraries.filter((item) => item.scope === "specialized"));

  $effect(() => {
    if (!selectionInitialized) {
      selected = defaultVocabularySelection(libraries, initialSelectedIds);
      selectionInitialized = true;
    }
  });

  async function confirm() {
    if (submitting) return;
    submitting = true;
    error = null;
    try {
      await onConfirm(selected);
    } catch (reason) {
      error = reason instanceof Error ? reason.message : String(reason);
    } finally {
      submitting = false;
    }
  }
</script>

<div class="backdrop" role="presentation">
  <div class="picker" role="dialog" aria-modal="true" aria-labelledby="vocabulary-picker-title">
    <header>
      <div>
        <h2 id="vocabulary-picker-title">选择本次会议词库</h2>
        <p title={audioName}>{audioName}</p>
      </div>
    </header>

    {#if error}<div class="error" role="alert">{error}</div>{/if}

    <div class="groups">
      <fieldset>
        <legend>通用词库 <small>默认选中</small></legend>
        {#each general as library (library.id)}
          <label class="option">
            <input type="checkbox" bind:group={selected} value={library.id} />
            <span><b>{library.name}</b><small>{library.terms.length} 个词</small></span>
          </label>
        {/each}
        {#if general.length === 0}<p class="empty">暂无通用词库</p>{/if}
      </fieldset>

      <fieldset>
        <legend>专用词库 <small>按会议选择</small></legend>
        {#each specialized as library (library.id)}
          <label class="option">
            <input type="checkbox" bind:group={selected} value={library.id} />
            <span><b>{library.name}</b><small>{library.terms.length} 个词</small></span>
          </label>
        {/each}
        {#if specialized.length === 0}<p class="empty">暂无专用词库</p>{/if}
      </fieldset>
    </div>

    <p class="help">只对这次转写生效。任务提交后会冻结当前词库内容。</p>
    <footer>
      <span>已选择 {selected.length} 个词库</span>
      <div class="actions">
        <button class="secondary" disabled={submitting} onclick={onCancel}>取消</button>
        <button class="primary" disabled={submitting} aria-busy={submitting} onclick={() => void confirm()}>{submitting ? "正在提交…" : "开始转写"}</button>
      </div>
    </footer>
  </div>
</div>

<style>
  .backdrop { position: fixed; inset: 0; z-index: 40; display: flex; align-items: center; justify-content: center; padding: 24px; background: rgba(0, 0, 0, .48); }
  .picker { box-sizing: border-box; width: min(620px, 100%); max-height: calc(100vh - 48px); overflow: auto; padding: var(--space-5); border: 1px solid var(--hairline); border-radius: var(--radius-card); background: var(--card); color: var(--fg); box-shadow: 0 18px 48px rgba(0, 0, 0, .28); }
  h2 { margin: 0 0 5px; font-size: 20px; }
  header p { overflow: hidden; margin: 0; color: var(--muted); font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
  .groups { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: var(--space-4); }
  fieldset { display: grid; align-content: start; gap: 8px; min-width: 0; margin: 0; padding: 12px; border: 1px solid var(--hairline); border-radius: var(--radius-btn); }
  legend { padding: 0 4px; font-size: 14px; font-weight: 650; }
  legend small { margin-left: 5px; color: var(--accent); font-weight: 500; }
  .option { display: flex; align-items: center; gap: 9px; min-height: 36px; padding: 4px 6px; border-radius: 6px; cursor: pointer; }
  .option:hover { background: color-mix(in srgb, var(--accent) 7%, transparent); }
  .option input { width: 16px; height: 16px; }
  .option span { display: flex; min-width: 0; flex: 1; justify-content: space-between; gap: 8px; }
  .option b { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .option small, .empty, .help, footer > span { color: var(--muted); font-size: 12px; }
  .empty { margin: 6px; }
  .help { margin: var(--space-3) 0 0; line-height: 1.5; }
  footer, .actions { display: flex; align-items: center; }
  footer { justify-content: space-between; gap: 12px; margin-top: var(--space-4); }
  .actions { gap: 8px; }
  button { min-height: 36px; border-radius: var(--radius-btn); padding: 0 16px; font: inherit; font-weight: 600; cursor: pointer; }
  button:focus-visible, input:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
  button:disabled { cursor: default; opacity: .55; }
  .secondary { border: 1px solid var(--hairline); background: var(--card); color: var(--fg); }
  .primary { border: 1px solid var(--accent); background: var(--accent); color: white; }
  .error { margin-top: var(--space-3); padding: 10px 12px; border-radius: var(--radius-btn); background: color-mix(in srgb, var(--danger) 10%, transparent); color: var(--danger); font-size: 13px; }
  @media (max-width: 640px) { .groups { grid-template-columns: 1fr; } footer { align-items: flex-start; flex-direction: column; } .actions { align-self: stretch; } .actions button { flex: 1; } }
</style>
