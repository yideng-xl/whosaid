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
  import type { createApi, VocabularyEntry, VocabularyGroups } from "./api";
  import Icon from "./Icon.svelte";

  let { api, onClose }: {
    api: ReturnType<typeof createApi>;
    onClose: () => void;
  } = $props();

  let personText = $state("");
  let termText = $state("");
  let otherText = $state("");
  let loading = $state(true);
  let saving = $state(false);
  let saved = $state(false);
  let error = $state<string | null>(null);

  const personCount = $derived(parseTerms(personText).length);
  const termCount = $derived(parseTerms(termText).length);
  const otherCount = $derived(parseTerms(otherText).length);
  const totalCount = $derived(personCount + termCount + otherCount);

  $effect(() => { void load(); });

  function fill(entries: VocabularyEntry[]) {
    personText = entries.filter((entry) => entry.kind === "person").map((entry) => entry.canonical).join("、");
    termText = entries.filter((entry) => entry.kind === "term").map((entry) => entry.canonical).join("、");
    otherText = entries.filter((entry) => entry.kind === "other").map((entry) => entry.canonical).join("、");
  }

  async function load() {
    loading = true;
    error = null;
    try {
      fill(await api.listVocabulary());
    } catch (reason) {
      error = `加载词库失败：${reason}`;
    } finally {
      loading = false;
    }
  }

  async function save() {
    if (saving) return;
    saving = true;
    saved = false;
    error = null;
    const groups: VocabularyGroups = {
      person: parseTerms(personText),
      term: parseTerms(termText),
      other: parseTerms(otherText),
    };
    try {
      fill(await api.replaceVocabulary(groups));
      saved = true;
    } catch (reason) {
      error = `保存失败：${reason}`;
    } finally {
      saving = false;
    }
  }
</script>

<section class="manager">
  <header>
    <div>
      <h1>词库</h1>
      <p>每个词之间用逗号或顿号隔开。保存后，新建的转写任务会自动使用这些标准写法。</p>
    </div>
    <button class="icon-button" aria-label="关闭词库" onclick={onClose}>
      <Icon name="close" size={16} />
    </button>
  </header>

  {#if error}<div class="error" role="alert">{error}</div>{/if}

  {#if loading}
    <div class="loading">正在加载词库…</div>
  {:else}
    <div class="fields">
      <label class="field">
        <span class="field-title">姓名 <small>{personCount} 个</small></span>
        <span class="field-help">会议中经常出现的人名</span>
        <textarea bind:value={personText} rows="5" placeholder="例如：许磊、张三、李四" oninput={() => (saved = false)}></textarea>
      </label>

      <label class="field">
        <span class="field-title">专用词库 <small>{termCount} 个</small></span>
        <span class="field-help">产品名、系统名、行业术语和缩写</span>
        <textarea bind:value={termText} rows="5" placeholder="例如：端到端探测、终端IP核查、WhoSaid" oninput={() => (saved = false)}></textarea>
      </label>

      <label class="field">
        <span class="field-title">其他 <small>{otherCount} 个</small></span>
        <span class="field-help">地点、组织或暂时不便分类的标准写法</span>
        <textarea bind:value={otherText} rows="5" placeholder="例如：陕西省调、产品部周例会" oninput={() => (saved = false)}></textarea>
      </label>
    </div>

    <footer>
      <span>共 {totalCount} 个词</span>
      <button class="primary" disabled={saving} onclick={() => void save()}>
        {saving ? "保存中…" : saved ? "已保存" : "保存词库"}
      </button>
    </footer>
  {/if}
</section>

<style>
  .manager { box-sizing: border-box; width: min(920px, 100%); margin: 0 auto; padding: var(--space-6); color: var(--fg); }
  header { display: flex; justify-content: space-between; align-items: flex-start; gap: var(--space-4); margin-bottom: var(--space-5); }
  h1 { margin: 0 0 6px; font-size: 24px; }
  header p { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.5; }
  .icon-button { border: 1px solid var(--hairline); border-radius: var(--radius-btn); background: var(--card); color: var(--fg); padding: 7px; cursor: pointer; }
  .fields { display: grid; gap: var(--space-4); }
  .field { display: grid; gap: 6px; padding: var(--space-4); border: 1px solid var(--hairline); border-radius: var(--radius-card); background: var(--card); }
  .field-title { font-size: 15px; font-weight: 650; }
  .field-title small { margin-left: 6px; color: var(--accent); font-size: 12px; font-weight: 500; }
  .field-help { color: var(--muted); font-size: 12px; }
  textarea { box-sizing: border-box; width: 100%; min-height: 104px; margin-top: 4px; resize: vertical; border: 1px solid var(--hairline); border-radius: var(--radius-btn); background: var(--bg); color: var(--fg); padding: 10px 12px; font: inherit; font-size: 14px; line-height: 1.7; }
  textarea:focus { outline: 2px solid var(--focus); outline-offset: 1px; }
  footer { display: flex; justify-content: space-between; align-items: center; margin-top: var(--space-4); color: var(--muted); font-size: 13px; }
  .primary { min-width: 112px; min-height: 36px; border: 1px solid var(--accent); border-radius: var(--radius-btn); background: var(--accent); color: #fff; padding: 0 16px; font: inherit; font-weight: 600; cursor: pointer; }
  .primary:disabled { opacity: .55; cursor: default; }
  .error { margin-bottom: var(--space-3); padding: 10px 12px; border-radius: var(--radius-btn); background: color-mix(in srgb, var(--danger) 10%, transparent); color: var(--danger); font-size: 13px; }
  .loading { padding: 40px; border: 1px dashed var(--hairline); border-radius: var(--radius-card); text-align: center; color: var(--muted); }
</style>
