<script lang="ts">
  import type { Speaker } from "./api";

  let {
    speakers = [],
    onRename,
    sampleUrl,
  }: {
    speakers: Speaker[];
    // 保存单个说话人的新名字：把原始标签 orig 与新名字回传给父组件调 api.rename
    onRename: (orig: string, name: string) => Promise<void>;
    // 给定原始标签，返回该说话人试听片段的 URL
    sampleUrl: (orig: string) => string;
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

  // ── 试听播放态机 ──────────────────────────────────────────────
  // 同一时刻只有一个音频在放：重复点击不再 new Audio() 叠加（那会出回声）。
  // 点▶ → loading(转圈) → playing(⏸ 可随时暂停) → 再点▶ 从暂停处继续；点别人先停当前。
  let audio: HTMLAudioElement | null = null;
  let activeOrig = $state<string | null>(null); // 当前加载/播放中的说话人原始标签
  let phase = $state<"loading" | "playing" | "paused">("paused");

  function ensureAudio(): HTMLAudioElement {
    if (audio) return audio;
    const a = new Audio();
    a.onplaying = () => (phase = "playing");
    a.onwaiting = () => (phase = "loading");
    a.onpause = () => {
      // 切换到别人时也会触发 pause，但那时已进入 loading，别把它覆盖回 paused
      if (phase !== "loading") phase = "paused";
    };
    a.onended = () => {
      activeOrig = null;
      phase = "paused";
    };
    a.onerror = () => {
      activeOrig = null;
      phase = "paused";
    };
    audio = a;
    return a;
  }

  function togglePlay(orig: string) {
    const a = ensureAudio();
    // 点正在播放的自己 → 暂停（phase 由 onpause 置）
    if (activeOrig === orig && phase === "playing") {
      a.pause();
      return;
    }
    // 点已暂停的自己 → 从暂停处继续
    if (activeOrig === orig && phase === "paused" && a.src) {
      void a.play().catch(() => {});
      return;
    }
    // 点别人 / 全新 → 停掉当前，加载新片段
    a.pause();
    activeOrig = orig;
    phase = "loading";
    a.src = sampleUrl(orig);
    a.load();
    void a.play().catch(() => {}); // 首帧就绪后自动播放；被拒则忽略
  }

  // 组件卸载（切任务/离开）时停音频，避免后台继续放
  $effect(() => {
    return () => {
      if (audio) {
        audio.pause();
        audio.src = "";
      }
    };
  });
</script>

{#if speakers.length}
  <div class="rename">
    <div class="title">说话人重命名</div>
    <div class="rows">
      {#each speakers as s (s.orig)}
        <div class="row">
          <span class="orig">{s.orig}</span>
          <button
            class="play"
            class:active={activeOrig === s.orig && phase !== "paused"}
            title="试听这位说话人的 3 段发言"
            disabled={activeOrig === s.orig && phase === "loading"}
            onclick={() => togglePlay(s.orig)}
          >
            {#if activeOrig === s.orig && phase === "loading"}
              <span class="spin"></span>
            {:else if activeOrig === s.orig && phase === "playing"}
              ⏸
            {:else}
              ▶
            {/if}
          </button>
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
  .play {
    flex-shrink: 0;
    width: 26px;
    height: 26px;
    padding: 0;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border: 1px solid var(--line, #d8d8dc);
    background: var(--input-bg, #fff);
    color: var(--fg, #333);
    border-radius: 50%;
    font-size: 11px;
    cursor: pointer;
  }
  .play:hover:not(:disabled) { border-color: var(--accent, #3b7ddd); color: var(--accent, #3b7ddd); }
  .play:disabled { cursor: default; }
  .play.active {
    border-color: var(--accent, #3b7ddd);
    background: var(--accent, #3b7ddd);
    color: #fff;
  }
  .spin {
    width: 11px;
    height: 11px;
    border: 2px solid currentColor;
    border-top-color: transparent;
    border-radius: 50%;
    display: inline-block;
    animation: play-spin 0.7s linear infinite;
  }
  @keyframes play-spin { to { transform: rotate(360deg); } }

  @media (prefers-color-scheme: dark) {
    .rename { --line: #2a2a2e; --card: #202024; --fg: #eaeaea; --input-bg: #17171a; }
  }
</style>
