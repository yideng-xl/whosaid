<script lang="ts">
  import Icon from "./Icon.svelte";
  import { recordingAudioSrc, type RecordingSnapshot } from "./recording";
  import type { PendingRecordingSubmission } from "./recordingFlow";
  import {
    formatElapsed,
    labelForPhase,
    recordingState,
    reduceRecordingState,
    sourceLabel,
  } from "./recordingState";

  let {
    snapshot,
    onStop,
    systemPermissionDenied = false,
    onOpenSystemSettings = () => {},
    pendingRecordings = [],
    onConfirmTranscription = () => {},
    toAudioSrc = recordingAudioSrc,
  }: {
    snapshot: RecordingSnapshot;
    onStop: () => void | Promise<void>;
    systemPermissionDenied?: boolean;
    onOpenSystemSettings?: () => void | Promise<void>;
    pendingRecordings?: PendingRecordingSubmission[];
    onConfirmTranscription?: (
      recording: PendingRecordingSubmission,
    ) => void | Promise<void>;
    toAudioSrc?: (path: string) => string;
  } = $props();

  let stopPending = $state(false);
  let settingsPending = $state(false);
  let confirmingPaths = $state<Set<string>>(new Set());
  const ui = $derived(reduceRecordingState(recordingState(), snapshot));
  const canStop = $derived(ui.phase === "recording");

  async function stopOnce() {
    if (stopPending || !canStop) return;
    stopPending = true;
    try {
      await onStop();
    } catch {
      // 停止命令失败时允许用户重试；具体错误由持有录音状态的上层展示。
      stopPending = false;
    }
  }

  async function openSettingsOnce() {
    if (settingsPending) return;
    settingsPending = true;
    try {
      await onOpenSystemSettings();
    } catch {
      // 上层负责展示具体错误；组件只保证按钮可再次操作。
    } finally {
      settingsPending = false;
    }
  }

  function fileName(path: string): string {
    const parts = path.split(/[\\/]/);
    return parts.at(-1) || path;
  }

  async function confirmOnce(recording: PendingRecordingSubmission) {
    const path = recording.finalPath.trim();
    if (!path || recording.busy || confirmingPaths.has(path)) return;
    confirmingPaths = new Set(confirmingPaths).add(path);
    try {
      await onConfirmTranscription(recording);
    } catch {
      const next = new Set(confirmingPaths);
      next.delete(path);
      confirmingPaths = next;
    }
  }
</script>

<section class="recording-panel" aria-label="直接录音">
  <div class="recording-card">
    <div class="heading">
      <span class="recording-mark" class:active={ui.phase === "recording"}>
        <Icon name="record" size={20} />
      </span>
      <div>
        <h1>{labelForPhase(ui.phase)}</h1>
        <div class="elapsed">{formatElapsed(ui.elapsed_seconds)}</div>
      </div>
    </div>

    <div class="sources" aria-label="录音来源">
      <div class="source" class:degraded={ui.system_audio !== "active"}>
        <span class="source-icon"><Icon name="computer-audio" size={20} /></span>
        <span class="source-copy">
          <strong>电脑声音</strong>
          <small>{sourceLabel(ui.system_audio)}</small>
        </span>
        <span class="source-dot {ui.system_audio}" aria-hidden="true"></span>
      </div>

      <div class="source" class:degraded={ui.microphone !== "active"}>
        <span class="source-icon"><Icon name="microphone" size={20} /></span>
        <span class="source-copy">
          <strong>麦克风</strong>
          <small>{sourceLabel(ui.microphone)}</small>
        </span>
        <span class="source-dot {ui.microphone}" aria-hidden="true"></span>
      </div>
    </div>

    {#if ui.warning}
      <div class="warning" role="status">
        <Icon name="warning" size={17} />
        <span>{ui.warning}</span>
      </div>
    {/if}

    {#if systemPermissionDenied}
      <div class="permission" role="status">
        <Icon name="computer-audio" size={18} />
        <div>
          <strong>需要允许系统录音</strong>
          <p>whosaid 只录声音，不保存屏幕画面。授权后如系统提示，请重新启动 whosaid。</p>
          <button
            disabled={settingsPending}
            aria-busy={settingsPending}
            onclick={openSettingsOnce}
          >打开系统设置</button>
        </div>
      </div>
    {/if}

    {#if ui.phase === "failed"}
      <div class="failure" role="alert">
        <strong>{ui.error ?? "录音未能完成"}</strong>
        {#if ui.recoverable_paths.length > 0}
          <p>已保留以下录音文件：</p>
          <ul>
            {#each ui.recoverable_paths as path (path)}
              <li>{path}</li>
            {/each}
          </ul>
        {/if}
      </div>
    {/if}

    {#if canStop}
      <button
        class="stop"
        disabled={stopPending}
        aria-busy={stopPending}
        onclick={stopOnce}
      >
        <Icon name="stop" size={16} />
        <span>{stopPending ? "正在停止…" : "停止并保存"}</span>
      </button>
    {:else if ["stopping", "mixing", "submitting"].includes(ui.phase)}
      <div class="working" aria-live="polite">
        <span class="spinner" aria-hidden="true"></span>
        {labelForPhase(ui.phase)}
      </div>
    {/if}

    {#if pendingRecordings.some((recording) => recording.finalPath.trim())}
      <div class="previews" aria-label="待确认录音">
        <h2>试听录音</h2>
        <p class="preview-hint">确认声音没有问题后，再开始分人和转写。</p>
        {#each pendingRecordings.filter((recording) => recording.finalPath.trim()) as recording (recording.finalPath)}
          <article class="preview-item">
            <strong>{recording.label || fileName(recording.finalPath)}</strong>
            <span class="preview-name">{fileName(recording.finalPath)}</span>
            <span class="preview-path">{recording.finalPath}</span>
            <audio
              controls
              preload="metadata"
              src={toAudioSrc(recording.finalPath)}
              aria-label={`试听${recording.label || fileName(recording.finalPath)}`}
            ></audio>
            {#if recording.error}
              <small class="preview-error" role="alert">{recording.error}</small>
            {/if}
            <button
              class="confirm"
              aria-label={`确认${recording.label || fileName(recording.finalPath)}无误，开始转写`}
              disabled={recording.busy || confirmingPaths.has(recording.finalPath.trim())}
              aria-busy={recording.busy || confirmingPaths.has(recording.finalPath.trim())}
              onclick={() => confirmOnce(recording)}
            >{recording.busy || confirmingPaths.has(recording.finalPath.trim()) ? "正在提交…" : "确认无误，开始转写"}</button>
          </article>
        {/each}
      </div>
    {/if}
  </div>
</section>

<style>
  .recording-panel {
    width: 100%;
    height: 100%;
    box-sizing: border-box;
    display: grid;
    place-items: center;
    padding: var(--space-6);
    background: var(--bg);
    color: var(--fg);
  }
  .recording-card {
    width: min(520px, 100%);
    box-sizing: border-box;
    padding: var(--space-6);
    border: 1px solid var(--hairline);
    border-radius: var(--radius-modal);
    background: var(--card);
  }
  .previews {
    margin-top: var(--space-5);
    display: grid;
    gap: var(--space-3);
  }
  .previews h2 { margin: 0; font-size: 16px; }
  .preview-hint { margin: calc(var(--space-2) * -1) 0 0; color: var(--muted); }
  .preview-item {
    display: grid;
    gap: var(--space-2);
    padding: var(--space-3);
    border: 1px solid var(--hairline);
    border-radius: var(--radius-card);
    background: color-mix(in srgb, var(--accent) 4%, var(--card));
  }
  .preview-name { font-size: 13px; }
  .preview-path {
    color: var(--muted);
    overflow-wrap: anywhere;
    font-family: ui-monospace, "SFMono-Regular", monospace;
    font-size: 11px;
  }
  .preview-item audio { width: 100%; }
  .preview-error { color: var(--danger); }
  .confirm {
    min-height: 38px;
    border: 1px solid var(--accent);
    border-radius: var(--radius-btn);
    background: var(--accent);
    color: #fff;
    font: inherit;
    font-weight: 600;
    cursor: pointer;
  }
  .confirm:disabled { cursor: default; opacity: 0.6; }
  .heading {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    margin-bottom: var(--space-5);
  }
  .recording-mark {
    width: 42px;
    height: 42px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: 50%;
    color: var(--muted);
    background: color-mix(in srgb, var(--muted) 10%, transparent);
  }
  .recording-mark.active {
    color: var(--danger);
    background: color-mix(in srgb, var(--danger) 12%, transparent);
  }
  h1 {
    margin: 0 0 2px;
    font-size: 20px;
    font-weight: 650;
  }
  .elapsed {
    color: var(--muted);
    font-size: 24px;
    font-variant-numeric: tabular-nums;
    letter-spacing: 0.03em;
  }
  .sources {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: var(--space-3);
  }
  .source {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-3);
    border: 1px solid var(--hairline);
    border-radius: var(--radius-card);
  }
  .source-icon {
    display: inline-flex;
    color: var(--fg);
  }
  .source-copy {
    min-width: 0;
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .source-copy strong { font-size: 13px; }
  .source-copy small { color: var(--muted); font-size: 12px; }
  .source-dot {
    width: 8px;
    height: 8px;
    flex: 0 0 auto;
    border-radius: 50%;
    background: var(--muted);
  }
  .source-dot.active { background: var(--accent); }
  .source-dot.unavailable,
  .source-dot.denied,
  .source-dot.interrupted { background: var(--spk-2); }
  .warning {
    display: flex;
    align-items: flex-start;
    gap: var(--space-2);
    margin-top: var(--space-3);
    padding: var(--space-3);
    border: 1px solid color-mix(in srgb, var(--spk-2) 38%, var(--hairline));
    border-radius: var(--radius-card);
    background: color-mix(in srgb, var(--spk-2) 10%, var(--card));
    color: var(--spk-2);
    line-height: 1.5;
  }
  .warning :global(svg) { flex: 0 0 auto; margin-top: 1px; }
  .permission {
    display: flex;
    align-items: flex-start;
    gap: var(--space-2);
    margin-top: var(--space-3);
    padding: var(--space-3);
    border: 1px solid color-mix(in srgb, var(--accent) 30%, var(--hairline));
    border-radius: var(--radius-card);
    background: color-mix(in srgb, var(--accent) 8%, var(--card));
    color: var(--fg);
  }
  .permission :global(svg) { flex: 0 0 auto; color: var(--accent); }
  .permission strong { font-size: 13px; }
  .permission p {
    margin: var(--space-1) 0 var(--space-2);
    color: var(--muted);
    line-height: 1.5;
  }
  .permission button {
    min-height: 34px;
    padding: 6px 14px;
    border: 1px solid var(--accent);
    border-radius: var(--radius-btn);
    background: var(--accent);
    color: #fff;
    font: inherit;
    font-weight: 600;
    cursor: pointer;
  }
  .permission button:disabled { cursor: default; opacity: 0.6; }
  .failure {
    margin-top: var(--space-3);
    padding: var(--space-3);
    border: 1px solid color-mix(in srgb, var(--danger) 30%, var(--hairline));
    border-radius: var(--radius-card);
    background: color-mix(in srgb, var(--danger) 8%, var(--card));
    color: var(--danger);
  }
  .failure p { margin: var(--space-2) 0 var(--space-1); }
  .failure ul { margin: 0; padding-left: 20px; }
  .failure li {
    margin-top: var(--space-1);
    overflow-wrap: anywhere;
    font-family: ui-monospace, "SFMono-Regular", monospace;
    font-size: 11px;
  }
  .stop,
  .working {
    width: 100%;
    min-height: 42px;
    box-sizing: border-box;
    margin-top: var(--space-5);
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--space-2);
    border-radius: var(--radius-btn);
    font: inherit;
    font-weight: 600;
  }
  .stop {
    border: 1px solid var(--danger);
    background: var(--danger);
    color: #fff;
    cursor: pointer;
  }
  .stop:hover:not(:disabled) {
    background: color-mix(in srgb, var(--danger) 86%, black);
  }
  .stop:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
  .stop:disabled { cursor: default; opacity: 0.6; }
  .working {
    color: var(--muted);
    background: color-mix(in srgb, var(--muted) 8%, transparent);
  }
  .spinner {
    width: 13px;
    height: 13px;
    border: 1.5px solid var(--hairline);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
