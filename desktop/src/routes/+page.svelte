<script lang="ts">
  import { onMount } from "svelte";
  import { invoke } from "@tauri-apps/api/core";
  import { getCurrentWebview } from "@tauri-apps/api/webview";
  import Sidebar from "$lib/Sidebar.svelte";
  import TranscriptView from "$lib/TranscriptView.svelte";
  import ModelManager from "$lib/ModelManager.svelte";
  import RecordingPanel from "$lib/RecordingPanel.svelte";
  import { createApi, type JobSummary } from "$lib/api";
  import {
    closeAfterRecording,
    getRecordingPermissions,
    getRecordingState,
    listRecoverableRecordings,
    manageAsyncListener,
    openRecordingSettings,
    recordingController,
    retryRecordingMix,
    watchRecordingCloseRequested,
    type RecoverableRecording,
    type RecordingPermissions,
    type RecordingSnapshot,
  } from "$lib/recording";
  import {
    createRecordingJob,
    finalizeAndSubmit,
    mergeBackendRecordingSnapshot,
    prependRecordingJob,
    RecordingSubmissionError,
    submitFinalizedRecording,
    type RecordingSubmissionResult,
  } from "$lib/recordingFlow";
  import { recordingState } from "$lib/recordingState";
  import { hasUndownloadedActiveModel } from "$lib/modelState";
  import { resolveInitialTheme, applyTheme, saveTheme, type Theme } from "$lib/theme";
  import "$lib/tokens.css";

  type Api = ReturnType<typeof createApi>;

  let api: Api | null = $state(null);
  let ready = $state(false);
  let statusText = $state("服务启动中…");
  let jobs = $state<JobSummary[]>([]);
  let selectedJobId = $state<string | null>(null);
  let view = $state<"transcript" | "models" | "recording">("transcript");
  let dragging = $state(false);
  let errorBanner = $state<string | null>(null);
  let modelsNotReady = $state(false);
  let firstRunDismissed = $state(false);
  let recordingSnapshot = $state<RecordingSnapshot>(recordingState());
  let recordingPermissions = $state<RecordingPermissions | null>(null);
  let recoverableRecordings = $state<RecoverableRecording[]>([]);
  let recordingActionPending = $state(false);
  let closeRequested = $state(false);
  let closePending = $state(false);
  let recordingEventRevision = 0;
  let ignoreRecordingEvents = false;
  let pageMounted = false;
  let finalizationInFlight: Promise<RecordingSubmissionResult> | null = null;
  // 深色/浅色主题：未手动选过时跟随系统，选过则覆盖系统并持久化到 localStorage
  let theme = $state<Theme>("light");

  const recordingActive = $derived([
    "requesting_permissions",
    "starting",
    "recording",
    "stopping",
    "mixing",
    "submitting",
  ].includes(recordingSnapshot.phase));
  const systemPermissionDenied = $derived(
    recordingPermissions?.systemAudio === "denied" ||
      recordingSnapshot.system_audio === "denied",
  );

  function toggleTheme() {
    theme = theme === "dark" ? "light" : "dark";
    applyTheme(theme);
    saveTheme(theme);
  }
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

  function messageOf(error: unknown): string {
    return error instanceof Error ? error.message : String(error);
  }

  function onRecordingSnapshot(snapshot: RecordingSnapshot) {
    recordingEventRevision += 1;
    // stop_recording 的 Promise 与最终 ready 事件跨 IPC 通道返回，ready 可能在前端已经
    // 进入 submitting 后才送达。此时不能把“正在提交”倒退回“录音已保存”。
    const mergedSnapshot = mergeBackendRecordingSnapshot(
      recordingSnapshot,
      snapshot,
      ignoreRecordingEvents,
    );
    if (mergedSnapshot === recordingSnapshot) return;
    recordingSnapshot = mergedSnapshot;
    if (snapshot.system_audio === "denied") {
      recordingPermissions = {
        systemAudio: "denied",
        microphone: recordingPermissions?.microphone ?? "notDetermined",
      };
    }
    if (["requesting_permissions", "starting", "recording", "stopping", "mixing"].includes(snapshot.phase)) {
      view = "recording";
    }
  }

  function acceptRecordingSubmission(result: RecordingSubmissionResult) {
    const job = createRecordingJob(result);
    jobs = prependRecordingJob(jobs, job);
    selectedJobId = job.id;
    view = "transcript";
    subscribe(job);
    ignoreRecordingEvents = true;
    recordingSnapshot = {
      ...recordingSnapshot,
      phase: "idle",
      elapsed_seconds: 0,
      final_path: null,
      recoverable_paths: [],
      error: null,
    };
  }

  function showRecordingFailure(error: unknown) {
    const finalPath =
      error instanceof RecordingSubmissionError ? error.finalPath : null;
    recordingSnapshot = {
      ...recordingSnapshot,
      phase: "failed",
      final_path: finalPath ?? recordingSnapshot.final_path,
      recoverable_paths: finalPath
        ? [finalPath]
        : recordingSnapshot.recoverable_paths,
      error: messageOf(error),
    };
    view = "recording";
  }

  async function performFinalization(): Promise<RecordingSubmissionResult> {
    if (!api) throw new Error("转写服务尚未就绪");
    recordingActionPending = true;
    view = "recording";
    try {
      const result = await finalizeAndSubmit(async () => {
        if (pageMounted) {
          recordingSnapshot = {
            ...recordingSnapshot,
            phase: "stopping",
            error: null,
          };
        }
        const stopped = await recordingController.stop();
        if (pageMounted) {
          ignoreRecordingEvents = true;
          recordingSnapshot = {
            ...recordingSnapshot,
            phase: "submitting",
            final_path: stopped.final_path,
            recoverable_paths: [stopped.final_path],
            error: null,
          };
        }
        return stopped;
      }, api);
      if (pageMounted) acceptRecordingSubmission(result);
      return result;
    } catch (error) {
      if (pageMounted) showRecordingFailure(error);
      throw error;
    } finally {
      if (pageMounted) recordingActionPending = false;
    }
  }

  function finalizeRecordingOnce(): Promise<RecordingSubmissionResult> {
    if (finalizationInFlight) return finalizationInFlight;
    const task = performFinalization();
    finalizationInFlight = task;
    // finally 会产生一个新的 Promise，显式消费其拒绝，避免页面销毁或按钮调用方
    // 不再等待时出现未处理拒绝。
    void task
      .finally(() => {
        if (finalizationInFlight === task) finalizationInFlight = null;
      })
      .catch(() => undefined);
    return task;
  }

  async function retryFinalSubmission() {
    if (!api || recordingActionPending || !recordingSnapshot.final_path) return;
    const finalPath = recordingSnapshot.final_path;
    recordingActionPending = true;
    recordingSnapshot = { ...recordingSnapshot, phase: "submitting", error: null };
    try {
      const result = await submitFinalizedRecording(finalPath, api);
      if (pageMounted) acceptRecordingSubmission(result);
    } catch (error) {
      if (pageMounted) showRecordingFailure(error);
      throw error;
    } finally {
      if (pageMounted) recordingActionPending = false;
    }
  }

  async function startDirectRecording() {
    if (recordingActionPending || recordingActive) return;
    ignoreRecordingEvents = false;
    recordingActionPending = true;
    view = "recording";
    recordingSnapshot = {
      ...recordingState(),
      phase: "requesting_permissions",
    };
    const revisionBeforeStart = recordingEventRevision;
    try {
      const permissions = await getRecordingPermissions();
      if (!pageMounted) return;
      recordingPermissions = permissions;
      if (permissions.systemAudio === "denied") {
        recordingSnapshot = {
          ...recordingState(),
          phase: "failed",
          system_audio: "denied",
          microphone: permissions.microphone === "denied" ? "denied" : "pending",
          error: "需要系统录音权限才能开始录音",
        };
        return;
      }

      const snapshot = await recordingController.start();
      if (pageMounted && recordingEventRevision === revisionBeforeStart) {
        recordingSnapshot = snapshot;
      }
    } catch (error) {
      if (!pageMounted) return;
      try {
        recordingPermissions = await getRecordingPermissions();
      } catch {
        // 保留原始启动错误；权限刷新只是为了决定是否显示设置入口。
      }
      if (recordingEventRevision === revisionBeforeStart) {
        showRecordingFailure(error);
      }
    } finally {
      if (pageMounted) recordingActionPending = false;
    }
  }

  function openRecordingEntry() {
    if (recordingSnapshot.final_path) {
      view = "recording";
      return;
    }
    void startDirectRecording();
  }

  async function recoverRecording(recording: RecoverableRecording) {
    if (!api || recordingActionPending) return;
    recordingActionPending = true;
    view = "recording";
    try {
      const mixed = await retryRecordingMix(recording.sessionId);
      if (!pageMounted) return;
      ignoreRecordingEvents = true;
      // 混音已经成功并清理了 .incomplete；即使后续提交失败，也不能再展示一个
      // 已不存在的恢复会话，而应改为展示最终 m4a 的重新提交入口。
      recoverableRecordings = recoverableRecordings.filter(
        (item) => item.sessionId !== recording.sessionId,
      );
      recordingSnapshot = {
        ...recordingSnapshot,
        phase: "submitting",
        final_path: mixed.final_path,
        recoverable_paths: [mixed.final_path],
        error: null,
      };
      const result = await submitFinalizedRecording(mixed.final_path, api);
      if (pageMounted) acceptRecordingSubmission(result);
    } catch (error) {
      if (!pageMounted) return;
      if (error instanceof RecordingSubmissionError) {
        showRecordingFailure(error);
      } else {
        recordingSnapshot = {
          ...recordingSnapshot,
          phase: "failed",
          final_path: null,
          recoverable_paths: [
            recording.sessionDir,
            recording.systemTrack,
            ...(recording.microphoneTrack ? [recording.microphoneTrack] : []),
          ],
          error: `恢复录音失败：${messageOf(error)}`,
        };
      }
      throw error;
    } finally {
      if (pageMounted) recordingActionPending = false;
    }
  }

  async function openSystemRecordingSettings() {
    try {
      await openRecordingSettings("systemAudio");
    } catch (error) {
      errorBanner = `无法打开系统设置：${messageOf(error)}`;
      throw error;
    }
  }

  async function stopSaveAndClose() {
    if (closePending) return;
    closePending = true;
    try {
      await finalizeRecordingOnce();
      await closeAfterRecording();
    } catch (error) {
      // 保存、混音或提交任何一步失败都留在当前窗口，保留恢复/重新提交入口。
      errorBanner = messageOf(error);
      closeRequested = false;
    } finally {
      if (pageMounted) closePending = false;
    }
  }

  function recoverableStartedAt(recording: RecoverableRecording): string {
    return new Intl.DateTimeFormat("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(new Date(recording.startedAt * 1000));
  }

  onMount(() => {
    let cancelled = false;
    let unlistenDrop: (() => void) | null = null;
    const mountedAt = Date.now();
    pageMounted = true;

    // 初始化主题：读 localStorage，没有则跟随系统偏好；写入 data-theme 使 CSS 规则生效
    theme = resolveInitialTheme();
    applyTheme(theme);

    const disposeRecordingState = manageAsyncListener(
      recordingController.watch((snapshot) => {
        if (!cancelled) onRecordingSnapshot(snapshot);
      }),
      (error) => {
        if (!cancelled) errorBanner = `录音状态监听失败：${messageOf(error)}`;
      },
    );
    const disposeCloseRequested = manageAsyncListener(
      watchRecordingCloseRequested(() => {
        if (!cancelled) closeRequested = true;
      }),
      (error) => {
        if (!cancelled) errorBanner = `退出保护监听失败：${messageOf(error)}`;
      },
    );

    // 录音状态、权限和遗留会话不依赖 Python 转写服务，启动即并行读取，不能阻塞
    // 现有任务列表和拖放入口。revision guard 避免晚到快照覆盖已收到的实时事件。
    const initialRevision = recordingEventRevision;
    void getRecordingState()
      .then((snapshot) => {
        if (cancelled || recordingEventRevision !== initialRevision) return;
        recordingSnapshot = snapshot;
        if (["requesting_permissions", "starting", "recording", "stopping", "mixing"].includes(snapshot.phase)) {
          view = "recording";
        }
      })
      .catch((error) => {
        if (!cancelled) errorBanner = `读取录音状态失败：${messageOf(error)}`;
      });
    void getRecordingPermissions()
      .then((permissions) => {
        if (!cancelled) recordingPermissions = permissions;
      })
      .catch(() => undefined);
    void listRecoverableRecordings()
      .then((recordings) => {
        if (!cancelled) recoverableRecordings = recordings;
      })
      .catch((error) => {
        if (!cancelled) errorBanner = `扫描未完成录音失败：${messageOf(error)}`;
      });

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

        // 2) 健康门：GET /models 成功即视为就绪；顺带记下模型列表，判断是否需要首次提示
        let modelsSnapshot: Awaited<ReturnType<typeof a.listModels>> = [];
        for (let i = 0; i < 20 && !cancelled; i++) {
          try {
            modelsSnapshot = await a.listModels();
            break;
          } catch {
            await new Promise((r) => setTimeout(r, 500));
          }
        }
        if (cancelled) return;
        modelsNotReady = hasUndownloadedActiveModel(modelsSnapshot);

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
      pageMounted = false;
      disposeRecordingState();
      disposeCloseRequested();
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
      const job = createRecordingJob({ jobId: id, audioPath: path });
      jobs = prependRecordingJob(jobs, job);
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
    {#if modelsNotReady && !firstRunDismissed}
      <div class="hint-banner">
        <span>还没有可用模型：请先在「模型管理」里填写 HuggingFace 访问令牌并下载模型，才能开始转写。</span>
        <button onclick={() => (view = "models")}>去设置</button>
        <button onclick={() => (firstRunDismissed = true)}>✕</button>
      </div>
    {/if}
    <Sidebar
      {jobs}
      {selectedJobId}
      {dragging}
      {onSelect}
      onOpenModels={() => (view = "models")}
      onStartRecording={openRecordingEntry}
      {recordingActive}
      recordingResultPending={Boolean(recordingSnapshot.final_path)}
      recordingElapsed={recordingSnapshot.elapsed_seconds}
      currentTheme={theme}
      onToggleTheme={toggleTheme}
      onDelete={(id) => {
        // 不直接删：先弹二次确认，避免误删丢失稿子
        const j = jobs.find((x) => x.id === id);
        deleteTarget = { id, name: j ? basename(j.audio_path) : id };
      }}
    />
    <main class="content">
      {#if recoverableRecordings.length > 0}
        {@const recovery = recoverableRecordings[0]}
        <div class="recovery-notice" role="status">
          <span>发现一段未完成录音（开始于 {recoverableStartedAt(recovery)}），可尝试恢复。</span>
          <button
            disabled={recordingActionPending || recordingActive}
            onclick={() => void recoverRecording(recovery).catch(() => undefined)}
          >恢复录音</button>
        </div>
      {/if}

      {#if view === "recording"}
        <RecordingPanel
          snapshot={recordingSnapshot}
          onStop={async () => { await finalizeRecordingOnce(); }}
          {systemPermissionDenied}
          onOpenSystemSettings={openSystemRecordingSettings}
          onRetrySubmit={recordingSnapshot.final_path ? retryFinalSubmission : undefined}
        />
      {:else if view === "models" && api}
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
          onRediarize={async (n) => {
            if (!api || !selectedJobId) return;
            const id = selectedJobId;
            try {
              await api.rediarize(id, n);
            } catch (e) {
              errorBanner = `重新分人失败：${e}`;
              return;
            }
            // 乐观置为运行态并重订阅：恢复侧栏实时进度 + 面板轮询（原 done 任务的 WS 已摘除）
            // rediarize 现在是全量重跑，从分人阶段重新开始，故乐观进度复位为 0（而非旧管线的 0.85）
            jobs = jobs.map((jb) => (jb.id === id ? { ...jb, status: "running", progress: 0 } : jb));
            watching.delete(id);
            const jb = jobs.find((x) => x.id === id);
            if (jb) subscribe(jb);
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

    {#if closeRequested}
      <div class="modal-backdrop" role="presentation">
        <div class="modal" role="dialog" aria-modal="true" aria-labelledby="recording-close-title">
          <div class="modal-title" id="recording-close-title">录音仍在进行</div>
          <p class="modal-body">关闭前需要先停止并保存录音。保存并提交成功后，whosaid 会自动关闭。</p>
          <div class="modal-actions">
            <button
              class="btn-cancel"
              disabled={closePending}
              onclick={() => (closeRequested = false)}
            >继续录音</button>
            <button
              class="btn-danger"
              disabled={closePending}
              aria-busy={closePending}
              onclick={stopSaveAndClose}
            >{closePending ? "正在停止并保存…" : "停止并保存"}</button>
          </div>
        </div>
      </div>
    {/if}
  </div>
{/if}

<style>
  /* 全局主题：背景/前景/字体统一走 token，深色由 :root[data-theme="dark"] 覆盖变量值，此处无需再重复硬编码。
     背景改透明：配合 tauri.conf.json 的 windows.transparent + Rust 侧 apply_vibrancy，
     让 NSVisualEffectView 的磨砂能透到窗口层；主区 .content 下方单独铺回不透明底，
     避免正文区域透出桌面导致文字不可读。 */
  :global(html, body) {
    margin: 0;
    height: 100%;
    background: transparent;
    color: var(--fg);
    font-family: var(--font);
    font-size: 13px;
  }
  .layout { display: flex; height: 100vh; }
  /* 主区保持不透明：只有侧栏透出磨砂，正文区仍需可读对比度 */
  .content { background: var(--bg); }
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
  .hint-banner {
    position: fixed;
    top: 0; left: 0; right: 0;
    z-index: 9;
    background: var(--accent);
    color: #fff;
    font-size: 13px;
    padding: 8px 14px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }
  .hint-banner button {
    background: transparent;
    border: 1px solid rgba(255, 255, 255, 0.6);
    border-radius: var(--radius-btn);
    color: #fff;
    cursor: pointer;
    font-size: 12px;
    padding: 3px 10px;
  }
  .content { flex: 1; overflow-y: auto; padding: 24px; box-sizing: border-box; }
  .placeholder { color: var(--muted); font-size: 14px; }
  .recovery-notice {
    margin-bottom: var(--space-3);
    padding: var(--space-3);
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    border: 1px solid color-mix(in srgb, var(--spk-2) 35%, var(--hairline));
    border-radius: var(--radius-card);
    background: color-mix(in srgb, var(--spk-2) 9%, var(--card));
    color: var(--fg);
  }
  .recovery-notice button {
    flex: 0 0 auto;
    padding: 6px 12px;
    border: 1px solid var(--spk-2);
    border-radius: var(--radius-btn);
    background: transparent;
    color: var(--spk-2);
    font: inherit;
    font-weight: 600;
    cursor: pointer;
  }
  .recovery-notice button:disabled { cursor: default; opacity: 0.55; }

  .boot {
    height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 14px;
    color: var(--muted);
    font-size: 14px;
  }
  .spinner {
    width: 26px; height: 26px;
    border: 3px solid var(--hairline);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* 删除二次确认：与 TranscriptView.svelte 的重新分人确认弹窗使用同一套 token 规范
     （圆角 12 / 柔和阴影 / 遮罩 40% 黑），仅浮层允许用阴影表达深度。颜色全部走全局
     token（tokens.css），双主题由 :root[data-theme] 统一驱动，此处不再单独覆盖深色值。 */
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
    background: var(--card);
    border-radius: var(--radius-modal);
    padding: var(--space-5);
    box-shadow: 0 8px 30px rgba(0, 0, 0, 0.16);
  }
  .modal-title {
    font-size: 15px;
    font-weight: 600;
    color: var(--fg);
    margin-bottom: var(--space-2);
  }
  .modal-body {
    font-size: 13px;
    line-height: 1.7;
    color: var(--muted);
    margin: 0 0 var(--space-4);
  }
  .modal-body b { color: var(--danger); }
  .modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: var(--space-2);
  }
  .modal-actions button {
    padding: 7px 16px;
    border-radius: var(--radius-btn);
    font: inherit;
    font-size: 13px;
    cursor: pointer;
    border: 1px solid transparent;
    transition: transform 0.12s ease, border-color 0.15s ease, background 0.15s ease;
  }
  .modal-actions button:active { transform: scale(0.97); }
  .modal-actions button:disabled { cursor: default; opacity: 0.6; }
  .modal-actions button:focus-visible {
    outline: 2px solid var(--focus);
    outline-offset: 1px;
  }
  .btn-cancel {
    background: transparent;
    border-color: var(--hairline);
    color: var(--fg);
  }
  .btn-cancel:hover { border-color: var(--muted); }
  .btn-danger {
    background: var(--danger);
    color: #fff;
  }
  .btn-danger:hover { opacity: 0.9; }
</style>
