<script lang="ts">
  import type { Speaker } from "./api";

  let {
    speakers = [],
    onRename,
  }: {
    speakers: Speaker[];
    // 保存单个说话人的新名字：把原始标签 orig 与新名字回传给父组件调 api.rename
    onRename: (orig: string, name: string) => Promise<void>;
  } = $props();

  // 每个原始标签对应的输入值，随 speakers 变化重置
  let draft = $state<Record<string, string>>({});
  let savingOrig = $state<string | null>(null);

  $effect(() => {
    const next: Record<string, string> = {};
    for (const s of speakers) next[s.orig] = s.name;
    draft = next;
  });

  async function save(orig: string) {
    const name = (draft[orig] ?? "").trim();
    if (!name) return;
    savingOrig = orig;
    try {
      await onRename(orig, name);
    } finally {
      savingOrig = null;
    }
  }
</script>

{#if speakers.length}
  <div class="rename">
    <div class="title">说话人重命名</div>
    <div class="rows">
      {#each speakers as s (s.orig)}
        <div class="row">
          <span class="orig">{s.orig}</span>
          <input
            bind:value={draft[s.orig]}
            placeholder={s.orig}
            onkeydown={(e) => e.key === "Enter" && save(s.orig)}
          />
          <button
            disabled={savingOrig === s.orig || (draft[s.orig] ?? "").trim() === s.name}
            onclick={() => save(s.orig)}
          >
            {savingOrig === s.orig ? "保存中…" : "改名"}
          </button>
        </div>
      {/each}
    </div>
  </div>
{/if}

<style>
  .rename {
    border: 1px solid var(--line, #e8e8ec);
    border-radius: 10px;
    padding: 12px 14px;
    margin-bottom: 16px;
    background: var(--card, #fafafa);
  }
  .title {
    font-size: 13px;
    font-weight: 600;
    color: var(--fg, #333);
    margin-bottom: 8px;
  }
  .rows { display: flex; flex-direction: column; gap: 8px; }
  .row { display: flex; align-items: center; gap: 8px; }
  .orig {
    font-size: 12px;
    color: var(--muted, #8a8a90);
    min-width: 64px;
  }
  .row input {
    flex: 1;
    padding: 5px 8px;
    border: 1px solid var(--line, #d8d8dc);
    border-radius: 6px;
    font: inherit;
    font-size: 13px;
    background: var(--input-bg, #fff);
    color: var(--fg, #1a1a1a);
  }
  .row button {
    padding: 5px 12px;
    border: 1px solid var(--accent, #3b7ddd);
    background: var(--accent, #3b7ddd);
    color: #fff;
    border-radius: 6px;
    font: inherit;
    font-size: 12px;
    cursor: pointer;
  }
  .row button:disabled {
    opacity: 0.45;
    cursor: default;
  }

  @media (prefers-color-scheme: dark) {
    .rename { --line: #2a2a2e; --card: #202024; --fg: #eaeaea; --input-bg: #17171a; }
  }
</style>
