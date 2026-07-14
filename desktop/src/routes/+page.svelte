<script lang="ts">
  import { onMount } from "svelte";
  import { invoke } from "@tauri-apps/api/core";
  import { getCurrentWebview } from "@tauri-apps/api/webview";
  import Sidebar from "$lib/Sidebar.svelte";
  import TranscriptView from "$lib/TranscriptView.svelte";
  import ModelManager from "$lib/ModelManager.svelte";
  import { createApi, type JobSummary } from "$lib/api";

  type Api = ReturnType<typeof createApi>;

  let api: Api | null = $state(null);
  let ready = $state(false);
  let statusText = $state("服务启动中…");
  let jobs = $state<JobSummary[]>([]);
  let selectedJobId = $state<string | null>(null);
  let view = $state<"transcript" | "models">("transcript");
  let dragging = $state(false);
  let errorBanner = $state<string | null>(null);
  // 待确认删除的任务（点 ✕ 先弹确认，删除会永久丢失文字稿/字幕稿，不可恢复）
  let deleteTarget = $state<{ id: string; name: string } | null>(null);

  function basename(p: string): string {
    const parts = p.split(/[\\/]/);
    return parts[parts.length - 1] || p;
  }

  async function confirmDelete() {
    if (!deleteTarget || !api) return;
    const id = deleteTarget.id;
    try {
      await api.deleteJob(id);
    } catch (e) {
      errorBanner = `删除失败：${e}`;
      deleteTarget = null;
      return;
    }
    jobs = jobs.filter((j) => j.id !== id);
    if (selectedJobId === id) selectedJobId = null;
    deleteTarget = null;
  }

  // 选中任务对象（供主区取文件名/状态）
  const selectedJob = $derived(jobs.find((j) => j.id === selectedJobId) ?? null);

  // 已订阅进度的 job，避免重复开 WS
  const watching = new Set<string>();

  onMount(() => {
    let cancelled = false;
    let unlistenDrop: (() => void) | null = null;
    const mountedAt = Date.now();

    // 0) 最先注册拖放监听：不依赖端口/服务，避免任何加载失败导致监听器注册不上。
    //    submit() 内部已 guard（api 未就绪时提示），所以早注册是安全的。
    getCurrentWebview()
      .onDragDropEvent((e) => {
        if (e.payload.type === "over" || e.payload.type === "enter") {
          dragging = true;
        } else if (e.payload.type === "leave") {
          dragging = false;
        } else if (e.payload.type === "drop") {
          const wasHovering = dragging;
          dragging = false;
          // 忽略启动瞬间的伪拖放事件（webview 重建时会重放上次的 drop，导致每次开
          // 应用都自动提交上次拖的文件）：真实拖放必先经 enter/over 使 dragging=true，
          // 且用户不可能在窗口刚出现的 2 秒内完成一次拖拽。二者任一不满足即视为伪事件。
          if (!wasHovering || Date.now() - mountedAt < 2000) return;
          try {
            for (const path of e.payload.paths) submit(path);
          } catch (err) {
            errorBanner = `处理拖入文件出错：${err}`;
          }
        }
      })
      .then((un) => {
        if (cancelled) un();
        else unlistenDrop = un;
      })
      .catch((err) => (errorBanner = `拖放监听注册失败：${err}`));

    (async () => {
      try {
        // 1) 轮询 Rust 侧拿服务端口（sidecar 起来并握手成功后返回非 null）
        let port: number | null = null;
        for (let i = 0; i < 120 && !cancelled; i++) {
          port = await invoke<number | null>("get_service_port");
          if (port) break;
          await new Promise((r) => setTimeout(r, 500));
        }
        if (cancelled) return;
        if (!port) {
          statusText = "服务未能启动，请检查 core venv 是否就绪";
          return;
        }
        const a = createApi(port);

        // 2) 健康门：GET /models 成功即视为就绪
        for (let i = 0; i < 20 && !cancelled; i++) {
          try {
            await a.listModels();
            break;
          } catch {
            await new Promise((r) => setTimeout(r, 500));
          }
        }
        if (cancelled) return;

        api = a;
        ready = true;

        // 3) 载入历史任务并对未完成的订阅进度（失败不影响拖放/界面）
        jobs = await a.listJobs();
        for (const j of jobs) subscribe(j);
      } catch (err) {
        errorBanner = `初始化出错：${err}`;
      }
    })();

    return () => {
      cancelled = true;
      if (unlistenDrop) unlistenDrop();
    };
  });

  function subscribe(job: JobSummary) {
    if (!api || watching.has(job.id)) return;
    if (job.status === "done" || job.status === "failed") return;
    watching.add(job.id);
    api.watchProgress(job.id, (m) => {
      jobs = jobs.map((j) =>
        j.id === job.id ? { ...j, status: m.status, progress: m.progress, error: m.error } : j
      );
      if (m.status === "done" || m.status === "failed") watching.delete(job.id);
    });
  }

  async function submit(path: string) {
    if (!api) return;
    // 只收音频文件，忽略其他误拖入
    if (!/\.(m4a|mp3|wav|aac|flac|mp4|mov|ogg)$/i.test(path)) {
      errorBanner = `不支持的文件类型（只收音频）：${path}`;
      return;
    }
    try {
      const id = await api.submitJob(path);
      const job: JobSummary = { id, status: "queued", progress: 0, error: null, audio_path: path };
      jobs = [job, ...jobs];
      selectedJobId = id;
      view = "transcript";
      subscribe(job);
    } catch (err) {
      errorBanner = `提交失败：${err}`;
    }
  }

  function onSelect(id: string) {
    selectedJobId = id;
    view = "transcript";
  }
</script>

<svelte:window
  onkeydown={(e) => {
    if (e.key === "Escape" && deleteTarget) deleteTarget = null;
  }}
/>

{#if !ready}
  <div class="boot">
    <div class="spinner"></div>
    <p>{statusText}</p>
  </div>
{:else}
  <div class="layout">
    {#if errorBanner}
      <div class="err-banner">
        <span>{errorBanner}</span>
        <button onclick={() => (errorBanner = null)}>✕</button>
      </div>
    {/if}
    <Sidebar
      {jobs}
      {selectedJobId}
      {dragging}
      {onSelect}
      onOpenModels={() => (view = "models")}
      onDelete={(id) => {
        // 不直接删：先弹二次确认，避免误删丢失稿子
        const j = jobs.find((x) => x.id === id);
        deleteTarget = { id, name: j ? basename(j.audio_path) : id };
      }}
    />
    <main class="content">
      {#if view === "models" && api}
        <ModelManager {api} onClose={() => (view = "transcript")} />
      {:else if api && selectedJobId}
        <TranscriptView
          {api}
          jobId={selectedJobId}
          audioPath={selectedJob?.audio_path ?? ""}
          status={selectedJob?.status ?? ""}
          onPause={async () => {
            try {
              await api!.pauseJob(selectedJobId!);
            } catch (e) {
              errorBanner = `暂停失败：${e}`;
              throw e; // 抛给 TranscriptView：请求即失败则立即恢复按钮
            }
          }}
          onResume={async () => {
            try {
              await api!.resumeJob(selectedJobId!);
            } catch (e) {
              errorBanner = `继续失败：${e}`;
              throw e;
            }
          }}
        />
      {:else}
        <div class="placeholder">从左侧选择一个任务，或把音频拖进窗口</div>
      {/if}
    </main>

    {#if deleteTarget}
      <div class="modal-backdrop" role="presentation">
        <div class="modal" role="dialog" aria-modal="true">
          <div class="modal-title">删除任务</div>
          <p class="modal-body">
            确定删除「{deleteTarget.name}」？<br />
            删除后<b>文字稿和字幕稿都会永久丢失、无法恢复</b>。
          </p>
          <div class="modal-actions">
            <button class="btn-cancel" onclick={() => (deleteTarget = null)}>取消</button>
            <button class="btn-danger" onclick={confirmDelete}>删除</button>
          </div>
        </div>
      </div>
    {/if}
  </div>
{/if}

<style>
  /* 全局主题：主区背景随明暗适配，避免深色模式下浅字落到白底看不清 */
  :global(html, body) {
    margin: 0;
    height: 100%;
    background: #ffffff;
    color: #1a1a1a;
  }
  @media (prefers-color-scheme: dark) {
    :global(html, body) {
      background: #1e1e21;
      color: #eaeaea;
    }
  }
  .layout { display: flex; height: 100vh; }
  .content { background: transparent; }
  .err-banner {
    position: fixed;
    top: 0; left: 0; right: 0;
    z-index: 10;
    background: #cf3b3b;
    color: #fff;
    font-size: 13px;
    padding: 8px 14px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }
  .err-banner button {
    background: transparent;
    border: none;
    color: #fff;
    cursor: pointer;
    font-size: 14px;
  }
  .content { flex: 1; overflow-y: auto; padding: 24px; box-sizing: border-box; }
  .placeholder { color: #9a9aa0; font-size: 14px; }

  .boot {
    height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 14px;
    color: #6a6a70;
    font-size: 14px;
  }
  .spinner {
    width: 26px; height: 26px;
    border: 3px solid #dcdce0;
    border-top-color: #3b7ddd;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* 删除二次确认 */
  .modal-backdrop {
    position: fixed;
    inset: 0;
    z-index: 20;
    background: rgba(0, 0, 0, 0.4);
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .modal {
    width: 340px;
    max-width: 90vw;
    background: #fff;
    border-radius: 12px;
    padding: 20px;
    box-shadow: 0 12px 40px rgba(0, 0, 0, 0.25);
  }
  .modal-title {
    font-size: 15px;
    font-weight: 600;
    color: #1a1a1a;
    margin-bottom: 10px;
  }
  .modal-body {
    font-size: 13px;
    line-height: 1.7;
    color: #4a4a4a;
    margin: 0 0 18px;
  }
  .modal-body b { color: #cf3b3b; }
  .modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: 10px;
  }
  .modal-actions button {
    padding: 7px 16px;
    border-radius: 7px;
    font: inherit;
    font-size: 13px;
    cursor: pointer;
    border: 1px solid transparent;
  }
  .btn-cancel {
    background: transparent;
    border-color: #d8d8dc;
    color: #333;
  }
  .btn-cancel:hover { border-color: #b0b0b6; }
  .btn-danger {
    background: #cf3b3b;
    color: #fff;
  }
  .btn-danger:hover { background: #b83232; }

  @media (prefers-color-scheme: dark) {
    .modal { background: #26262a; box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5); }
    .modal-title { color: #eaeaea; }
    .modal-body { color: #c4c4c8; }
    .btn-cancel { border-color: #3a3a40; color: #d0d0d4; }
    .btn-cancel:hover { border-color: #55555c; }
  }
</style>
