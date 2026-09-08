<script lang="ts">
  import { onMount } from "svelte";
  import { setRecordingOverlayEnabled, watchRecordingOverlay } from "./recordingOverlay";
  let enabled = $state(false);
  let pending = $state(false);
  let ready = $state(false);
  let error = $state("");
  onMount(() => watchRecordingOverlay(value => { enabled = value; ready = true; }, () => { error = "悬浮窗开关暂不可用"; }));
  async function toggle() {
    if (pending || !ready) return;
    pending = true;
    error = "";
    try { await setRecordingOverlayEnabled(!enabled); }
    catch { error = "未能切换悬浮窗，请重试"; }
    finally { pending = false; }
  }
</script>

<div class="overlay-option">
  <button role="switch" aria-checked={enabled} disabled={pending || !ready} onclick={toggle} title="仅在录音时显示置顶波形窗，不影响录音">
    <span>波形悬浮窗</span><span class="track" class:on={enabled} aria-hidden="true"><span></span></span>
  </button>
  {#if error}<small role="status">{error}</small>{/if}
</div>

<style>
  .overlay-option { margin-left: auto; }
  button { display: flex; align-items: center; gap: 8px; min-height: 28px; padding: 2px; border: 0; background: transparent; color: var(--fg); font: inherit; font-size: 12px; cursor: pointer; }
  button:disabled { cursor: default; opacity: .6; }
  button:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; border-radius: 4px; }
  .track { display: flex; align-items: center; width: 30px; height: 18px; border-radius: 10px; padding: 2px; box-sizing: border-box; background: var(--muted); }
  .track > span { width: 14px; height: 14px; border-radius: 50%; background: var(--bg); }
  .track.on { background: var(--accent); justify-content: flex-end; }
  small { display: block; margin-top: 4px; color: var(--danger); font-size: 12px; }
</style>
