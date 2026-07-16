<script lang="ts">
  import type { Speaker } from "./api";
  import Icon from "./Icon.svelte";

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
            aria-label={activeOrig === s.orig && phase === "loading"
              ? "试听音频加载中"
              : activeOrig === s.orig && phase === "playing"
                ? "暂停试听"
                : "试听这位说话人的发言"}
            disabled={activeOrig === s.orig && phase === "loading"}
            onclick={() => togglePlay(s.orig)}
          >
            {#if activeOrig === s.orig && phase === "loading"}
              <span class="spin"></span>
            {:else if activeOrig === s.orig && phase === "playing"}
              <Icon name="pause" size={12} />
            {:else}
              <Icon name="play" size={12} />
            {/if}
          </button>
          <input
            bind:value={draft[s.orig]}
            placeholder={s.orig}
            onkeydown={(e) => e.key === "Enter" && save(s.orig)}
          />
          <button
            aria-label="保存说话人改名"
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
  /* 卡片容器：token 化边框/圆角/底色，深浅主题由全局 tokens.css 的 data-theme 驱动，
     不再需要组件内自定义深色覆盖块。 */
  .rename {
    border: 1px solid var(--hairline);
    border-radius: var(--radius-card);
    padding: 12px 14px;
    margin-bottom: 16px;
    background: var(--card);
  }
  .title {
    font-size: 13px;
    font-weight: 600;
    color: var(--fg);
    margin-bottom: 8px;
  }
  .rows { display: flex; flex-direction: column; gap: 8px; }
  .row { display: flex; align-items: center; gap: 8px; }
  .orig {
    font-size: 12px;
    color: var(--muted);
    min-width: 64px;
  }
  /* Apple 输入框：细描边、圆角 6，聚焦时用 --focus 焦点环，与 TranscriptView 的输入框规范一致 */
  .row input {
    flex: 1;
    padding: 5px 8px;
    border: 1px solid var(--hairline);
    border-radius: var(--radius-btn);
    font: inherit;
    font-size: 13px;
    background: var(--card);
    color: var(--fg);
    transition: border-color 0.15s ease;
  }
  .row input:focus-visible {
    outline: 2px solid var(--focus);
    outline-offset: 1px;
  }
  .row button {
    padding: 5px 12px;
    border: 1px solid var(--accent);
    background: var(--accent);
    color: #fff;
    border-radius: var(--radius-btn);
    font: inherit;
    font-size: 12px;
    cursor: pointer;
    transition: transform 0.12s ease, opacity 0.15s ease;
  }
  .row button:hover:not(:disabled) { opacity: 0.9; }
  .row button:active:not(:disabled) { transform: scale(0.97); }
  .row button:focus-visible {
    outline: 2px solid var(--focus);
    outline-offset: 1px;
  }
  .row button:disabled {
    opacity: 0.45;
    cursor: default;
  }
  /* 试听圆形按钮：描边随主题走 --hairline，hover/激活态转 accent，按压 scale(0.97) */
  .play {
    flex-shrink: 0;
    width: 26px;
    height: 26px;
    padding: 0;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border: 1px solid var(--hairline);
    background: var(--card);
    color: var(--fg);
    border-radius: 50%;
    cursor: pointer;
    transition: border-color 0.15s ease, color 0.15s ease, background 0.15s ease, transform 0.12s ease;
  }
  .play:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
  .play:active:not(:disabled) { transform: scale(0.97); }
  .play:disabled { cursor: default; }
  .play:focus-visible {
    outline: 2px solid var(--focus);
    outline-offset: 1px;
  }
  .play.active {
    border-color: var(--accent);
    background: var(--accent);
    color: #fff;
  }
  /* loading 转圈：沿用原有 spinner CSS，颜色随 currentColor 自适应，不改动状态机 */
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
  @media (prefers-reduced-motion: reduce) {
    .spin { animation: none; }
  }
</style>
