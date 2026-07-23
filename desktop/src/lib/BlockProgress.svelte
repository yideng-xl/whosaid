<!-- 块状分段进度条：用 ▰(实心)/▱(空心) 字符渲染分段进度，配合百分比与「已完成/总数」文案。
     分离阶段(diarizing)不可中断且粒度粗，不显示块数/百分比，只提示「正在分离说话人…」；
     转写阶段(transcribing)按发言块数逐段推进，用本组件展示。 -->
<script lang="ts">
  let { total = 0, done = 0, phase = 'transcribing' }:
    { total: number; done: number; phase: 'diarizing' | 'transcribing' } = $props();

  const CELLS = 24;
  let pct = $derived(total > 0 ? Math.round((done / total) * 100) : 0);
  let filled = $derived(Math.round((pct / 100) * CELLS));
  let bar = $derived('▰'.repeat(filled) + '▱'.repeat(CELLS - filled));
</script>

{#if phase === 'diarizing'}
  <div class="bp diarizing">说话人分离中…</div>
{:else}
  <div class="bp">
    <span class="bar">{bar}</span>
    <span class="pct">{pct}%</span>
    <span class="count">{done}/{total} 段</span>
  </div>
{/if}

<style>
  .bp {
    font-variant-numeric: tabular-nums;
    font-family: ui-monospace, monospace;
  }
  .bar {
    letter-spacing: 1px;
  }
  .pct {
    font-weight: 600;
  }
  .count {
    opacity: 0.6;
    margin-left: 0.5em;
    font-size: 0.85em;
  }
</style>
