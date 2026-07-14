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

  // 选中任务对象（供主区取文件名/状态）
  const selectedJob = $derived(jobs.find((j) => j.id === selectedJobId) ?? null);

  // 已订阅进度的 job，避免重复开 WS
  const watching = new Set<string>();

  onMount(() => {
    let cancelled = false;
    let unlistenDrop: (() => void) | null = null;

    // 0) 最先注册拖放监听：不依赖端口/服务，避免任何加载失败导致监听器注册不上。
    //    submit() 内部已 guard（api 未就绪时提示），所以早注册是安全的。
    getCurrentWebview()
      .onDragDropEvent((e) => {
        if (e.payload.type === "over" || e.payload.type === "enter") {
          dragging = true;
        } else if (e.payload.type === "leave") {
          dragging = false;
        } else if (e.payload.type === "drop") {
          dragging = false;
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
            }
          }}
          onResume={async () => {
            try {
              await api!.resumeJob(selectedJobId!);
            } catch (e) {
              errorBanner = `继续失败：${e}`;
            }
          }}
        />
      {:else}
        <div class="placeholder">从左侧选择一个任务，或把音频拖进窗口</div>
      {/if}
    </main>
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
</style>
