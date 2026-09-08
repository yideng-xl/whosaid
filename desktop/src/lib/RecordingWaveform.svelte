<script lang="ts">
  import { onMount } from "svelte";
  import { listen } from "@tauri-apps/api/event";
  import { manageAsyncListener } from "./recording";
  import { appendWaveform, validLevel, waveformHeight, LEVEL_MAX_AGE_MS, WAVEFORM_SAMPLES, type AudioLevel } from "./recordingWaveform";

  let { source, label, active, compact = false }: { source: AudioLevel["source"]; label: string; active: boolean; compact?: boolean } = $props();
  let history = $state<number[]>(Array(WAVEFORM_SAMPLES).fill(0));
  let peak = $state(0);
  let receiving = $state(false);
  let paused = $state(false);
  let reducedMotion = $state(false);
  let unavailable = $state(false);
  let latest: AudioLevel | null = null;
  let since = Date.now();

  function resetWaveform(_active: boolean) {
    // 包括断流后重连，不能继续展示上一段的音量。
    since = Date.now();
    latest = null;
    history = Array(WAVEFORM_SAMPLES).fill(0);
    peak = 0;
    receiving = false;
  }
  $effect(() => resetWaveform(active));

  const levelText = $derived(!active ? "未采集" : unavailable ? "波形暂不可用" : !receiving ? "暂无音频数据" : peak <= 0.001 ? "低于 -60 dBFS" : `${Math.round(20 * Math.log10(peak))} dBFS`);

  onMount(() => {
    const media = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    const updateMotion = () => { reducedMotion = media?.matches ?? false; };
    updateMotion();
    media?.addEventListener("change", updateMotion);
    const dispose = manageAsyncListener(listen<unknown>("recording://level", ({ payload }) => {
      if (active && validLevel(payload, source, since, Date.now()) && (!latest || payload.sampledAt >= latest.sampledAt)) latest = payload;
    }), () => { unavailable = true; });
    const timer = window.setInterval(() => {
      if (!active) return;
      receiving = latest !== null && Date.now() - latest.sampledAt <= LEVEL_MAX_AGE_MS;
      peak = receiving && latest ? latest.peak : 0;
      if (!receiving) history = Array(WAVEFORM_SAMPLES).fill(0);
      else if (!paused) history = reducedMotion
        ? Array(WAVEFORM_SAMPLES).fill(waveformHeight(peak))
        : appendWaveform(history, peak);
    }, 100);
    return () => {
      clearInterval(timer);
      dispose();
      media?.removeEventListener("change", updateMotion);
    };
  });
</script>

<div class="waveform" class:compact aria-label={`${label}实时波形`} title={compact ? `${label}：${levelText}` : undefined}>
  <svg viewBox="0 0 300 52" preserveAspectRatio="none" aria-hidden="true">
    <line x1="0" y1="26" x2="300" y2="26" class="baseline" />
    {#each history as height, index}
      <line x1={index * 5 + 2.5} x2={index * 5 + 2.5} y1={26 - height * 24} y2={26 + height * 24} class="sample" />
    {/each}
  </svg>
  {#if !compact}<div class="caption">
    <span>{levelText}</span>
    <button disabled={!active || reducedMotion} aria-label={`${paused ? "继续" : "暂停"}${label}波形显示`} aria-pressed={paused} onclick={() => paused = !paused}>
      {reducedMotion ? "静态音量" : paused ? "继续波形" : "暂停波形"}
    </button>
  </div>{/if}
</div>

<style>
  .waveform { width: 100%; min-width: 0; }
  svg { display: block; width: 100%; height: 52px; overflow: hidden; }
  .compact svg { height: 18px; }
  .baseline { stroke: var(--hairline); stroke-width: 1; }
  .sample { stroke: var(--accent); stroke-width: 2; }
  .caption { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 4px; color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; }
  button { border: 0; padding: 4px; background: transparent; color: var(--muted); font: inherit; cursor: pointer; }
  button:hover:enabled { color: var(--fg); }
  button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  button:disabled { cursor: default; }
</style>
