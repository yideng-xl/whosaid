<script lang="ts">
  import type { NameCandidate } from "./api";

  type Row = {
    id: string;
    source: string;
    target: string;
    selected: boolean;
    manual: boolean;
    count: number;
  };

  let {
    candidates = [],
    onReplace,
  }: {
    candidates: NameCandidate[];
    onReplace: (mapping: Record<string, string>) => Promise<number>;
  } = $props();

  let rows = $state<Row[]>([]);
  let syncedCandidates = $state("");
  let saving = $state(false);
  let feedback = $state("");
  let error = $state("");
  let manualSeq = 0;

  // 只有候选内容真的变化时才重建行，避免父组件普通重渲染覆盖用户正在填写的目标名。
  $effect(() => {
    const signature = JSON.stringify(candidates);
    if (signature === syncedCandidates) return;
    syncedCandidates = signature;
    rows = candidates.map((candidate) => ({
      id: `candidate-${candidate.term}`,
      source: candidate.term,
      target: candidate.term,
      selected: false,
      manual: false,
      count: candidate.count,
    }));
  });

  const mapping = $derived.by(() => {
    const result: Record<string, string> = {};
    for (const row of rows) {
      const source = row.source.trim();
      const target = row.target.trim();
      if (row.selected && source && target && source !== target) result[source] = target;
    }
    return result;
  });

  function addManual() {
    manualSeq += 1;
    rows = [
      ...rows,
      {
        id: `manual-${manualSeq}`,
        source: "",
        target: "",
        selected: true,
        manual: true,
        count: 0,
      },
    ];
    feedback = "";
  }

  async function apply() {
    if (!Object.keys(mapping).length) return;
    saving = true;
    feedback = "";
    error = "";
    try {
      const replaced = await onReplace(mapping);
      feedback = `已替换 ${replaced} 处`;
      rows = rows.map((row) => ({ ...row, selected: false }));
    } catch (e) {
      error = `替换失败：${e}`;
    } finally {
      saving = false;
    }
  }
</script>

<div class="replace-card">
  <div class="header">
    <div>
      <div class="title">人名统一替换</div>
      <div class="desc">候选仅供参考，勾选并确认目标名后才会修改全文。</div>
    </div>
    <button class="add" onclick={addManual}>添加漏掉的人名</button>
  </div>

  {#if rows.length}
    <div class="rows">
      {#each rows as row (row.id)}
        <div class="row">
          <input
            class="select"
            type="checkbox"
            aria-label={row.manual ? "选择手动人名" : `选择 ${row.source}`}
            bind:checked={row.selected}
          />
          {#if row.manual}
            <input class="term" aria-label="手动原词" placeholder="原词" bind:value={row.source} />
          {:else}
            <span class="term-label">{row.source}</span>
          {/if}
          <span class="arrow">→</span>
          <input
            class="target"
            aria-label={row.manual ? "手动目标名" : `将 ${row.source} 替换为`}
            placeholder="统一为"
            bind:value={row.target}
          />
          {#if row.manual}
            <span class="count">手动</span>
          {:else}
            <span class="count">出现 {row.count} 次</span>
          {/if}
        </div>
      {/each}
    </div>
  {:else}
    <div class="empty">暂未识别到候选，可手动添加。</div>
  {/if}

  <div class="footer">
    <div class="message">
      {#if error}<span class="error">{error}</span>{:else}{feedback}{/if}
    </div>
    <button
      class="apply"
      disabled={saving || !Object.keys(mapping).length}
      onclick={apply}
    >
      {saving ? "替换中…" : "统一替换"}
    </button>
  </div>
</div>

<style>
  .replace-card {
    border: 1px solid var(--hairline);
    border-radius: var(--radius-card);
    padding: var(--space-3) 14px;
    margin-bottom: var(--space-4);
    background: var(--card);
  }
  .header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: var(--space-3);
  }
  .title {
    color: var(--fg);
    font-size: 13px;
    font-weight: 600;
  }
  .desc, .empty, .count, .message {
    color: var(--muted);
    font-size: 12px;
  }
  .desc { margin-top: 3px; }
  .empty { padding: var(--space-3) 0; }
  .rows {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    margin-top: var(--space-3);
  }
  .row {
    display: grid;
    grid-template-columns: 18px minmax(80px, 1fr) 18px minmax(100px, 1fr) 72px;
    align-items: center;
    gap: var(--space-2);
  }
  .select { accent-color: var(--accent); }
  .term-label {
    color: var(--fg);
    font-size: 13px;
  }
  .arrow {
    color: var(--muted);
    text-align: center;
  }
  .row input.term, .row input.target {
    min-width: 0;
    box-sizing: border-box;
    padding: 5px var(--space-2);
    border: 1px solid var(--hairline);
    border-radius: var(--radius-btn);
    background: var(--card);
    color: var(--fg);
    font: inherit;
    font-size: 13px;
  }
  .row input:focus-visible, button:focus-visible {
    outline: 2px solid var(--focus);
    outline-offset: 1px;
  }
  .count { text-align: right; }
  .footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    margin-top: var(--space-3);
  }
  .message { min-height: 18px; }
  .error { color: var(--danger); }
  button {
    border-radius: var(--radius-btn);
    font: inherit;
    cursor: pointer;
    transition: opacity 0.15s ease, transform 0.12s ease;
  }
  button:active:not(:disabled) { transform: scale(0.97); }
  .add {
    padding: 5px 10px;
    border: 1px solid var(--hairline);
    background: transparent;
    color: var(--accent);
    font-size: 12px;
  }
  .add:hover { border-color: var(--accent); }
  .apply {
    padding: 6px 14px;
    border: 1px solid var(--accent);
    background: var(--accent);
    color: #fff;
    font-size: 12px;
  }
  .apply:hover:not(:disabled) { opacity: 0.9; }
  .apply:disabled { cursor: default; opacity: 0.45; }
</style>
