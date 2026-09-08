<script lang="ts">
  import { onMount } from "svelte";
  import { invoke } from "@tauri-apps/api/core";
  import { getCurrentWindow } from "@tauri-apps/api/window";
  import RecordingWaveform from "$lib/RecordingWaveform.svelte";
  import { getRecordingState, watchRecording, manageAsyncListener, type RecordingSnapshot } from "$lib/recording";
  import { recordingState, formatElapsed } from "$lib/recordingState";
  import { watchRecordingOverlay } from "$lib/recordingOverlay";
  import { applyTheme, resolveInitialTheme } from "$lib/theme";
  import "$lib/tokens.css";
  let snapshot = $state<RecordingSnapshot>(recordingState());
  let enabled = $state(false);
  let error = $state("");
  const recording = $derived(enabled && snapshot.phase === "recording");
  let dragOrigin: { x: number; y: number } | null = null;
  function pointerDown(event: PointerEvent) {
    dragOrigin = event.button === 0 ? { x: event.screenX, y: event.screenY } : null;
  }
  function pointerMove(event: PointerEvent) {
    if (!dragOrigin || !(event.buttons & 1)) return;
    if (Math.hypot(event.screenX - dragOrigin.x, event.screenY - dragOrigin.y) < 5) return;
    dragOrigin = null;
    void getCurrentWindow().startDragging().catch(() => { error = "暂时无法移动悬浮条"; });
  }
  async function openMain() {
    dragOrigin = null;
    try { await invoke("open_recording_main_window"); }
    catch { error = "无法显示主窗口，请从 Dock 打开 whosaid"; }
  }
  onMount(() => {
    applyTheme(resolveInitialTheme());
    const themeChanged = () => applyTheme(resolveInitialTheme());
    window.addEventListener("storage", themeChanged);
    let disposed = false;
    let revision = 0;
    const disposeState = manageAsyncListener(watchRecording(value => {
      revision++;
      snapshot = value;
    }).then(unlisten => {
      const before = revision;
      void getRecordingState().then(value => {
        if (!disposed && revision === before) snapshot = value;
      }).catch(() => { if (!disposed) error = "录音状态暂不可用"; });
      return unlisten;
    }), () => { error = "录音状态暂不可用"; });
    const disposeSetting = watchRecordingOverlay(value => { enabled = value; }, () => { error = "悬浮窗状态暂不可用"; });
    return () => { disposed = true; disposeState(); disposeSetting(); window.removeEventListener("storage", themeChanged); };
  });
</script>

<div class="strip" role="button" tabindex="0" aria-label="录音波形悬浮窗，双击打开 whosaid" title={error || "双击打开 whosaid · 拖动移动 · 上方电脑声音，下方麦克风"}
  onpointerdown={pointerDown} onpointermove={pointerMove} onpointerup={() => dragOrigin = null} onpointercancel={() => dragOrigin = null}
  ondblclick={openMain} onkeydown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); void openMain(); } }}>
  <span class="elapsed" aria-label={`录音时间 ${formatElapsed(snapshot.elapsed_seconds)}`}>{formatElapsed(snapshot.elapsed_seconds)}</span>
  <div class="waves">
    {#if recording}
      <RecordingWaveform source="system" label="电脑声音" active={snapshot.system_audio === "active"} compact />
      <RecordingWaveform source="microphone" label="麦克风" active={snapshot.microphone === "active"} compact />
    {:else}
      <div class="flat" aria-label={error || "未在录音"}></div><div class="flat"></div>
    {/if}
  </div>
</div>

<style>
  :global(html), :global(body) { background: transparent; }
  :global(body) { margin: 0; color: var(--fg); font-family: -apple-system, BlinkMacSystemFont, sans-serif; }
  .strip { display: grid; grid-template-columns: 62px 1fr; gap: 10px; align-items: center; height: 60px; padding: 8px 18px; box-sizing: border-box; cursor: move; user-select: none; border-radius: 30px; overflow: hidden; background: var(--card); border: 1px solid var(--hairline); }
  .strip:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
  .elapsed { font-size: 14px; font-weight: 600; font-variant-numeric: tabular-nums; }
  .waves { display: grid; gap: 4px; min-width: 0; }
  .waves :global(*) { pointer-events: none; }
  .flat { height: 18px; background: linear-gradient(var(--hairline), var(--hairline)) center / 100% 1px no-repeat; }
</style>
