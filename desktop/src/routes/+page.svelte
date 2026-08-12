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
    type RecordingPermissions,
    type RecordingSnapshot,
  } from "$lib/recording";
  import {
    beginRecoverableRecording,
    completeAcceptedRecordingSubmission,
    completeRecoverableRecording,
    createRecordingJob,
    failRecoverableRecording,
    makeRecoverableRecordingItems,
    prependRecordingJob,
    RecordingCloseGuard,
    RecordingSnapshotCoordinator,
    RecordingSubmissionError,
    RecordingSubmissionRegistry,
    retainFailedRecordingSubmission,
    runRecordingCloseFlow,
    transitionRecordingSubmissionFailure,
    upsertPendingRecordingSubmission,
    validateFinalPath,
    type PendingRecordingSubmission,
    type RecoverableRecordingItem,
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
  let recoverableRecordings = $state<RecoverableRecordingItem[]>([]);
  let pendingRecordingSubmissions = $state<PendingRecordingSubmission[]>([]);
  let recordingActionPending = $state(false);
  let closeRequested = $state(false);
  let closePending = $state(false);
  let ignoreRecordingEvents = false;
  let submissionFailureFinalPath: string | null = null;
  let pageMounted = false;
  let finalizationInFlight: Promise<RecordingSubmissionResult> | null = null;
  const recordingSnapshots = new RecordingSnapshotCoordinator(recordingState());
  const recordingSubmissions = new RecordingSubmissionRegistry();
  const recordingClose = new RecordingCloseGuard();
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

  function publishRecordingSnapshot(snapshot: RecordingSnapshot) {
    recordingSnapshots.publishLocal(snapshot);
    recordingSnapshot = recordingSnapshots.snapshot;
  }

  function onRecordingSnapshot(snapshot: RecordingSnapshot) {
    // stop_recording 的 Promise 与最终 ready 事件跨 IPC 通道返回，ready 可能在前端已经
    // 进入 submitting 后才送达。必须先merge，只有接受的快照才增加revision并唤醒等待者。
    const accepted = recordingSnapshots.publishBackend(
      snapshot,
      ignoreRecordingEvents
        ? true
        : {
            submissionFailureTerminal: Boolean(submissionFailureFinalPath),
            submissionFailureFinalPath,
          },
    );
    if (!accepted) return;
    recordingSnapshot = recordingSnapshots.snapshot;
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

  function acceptSubmissionResult(result: RecordingSubmissionResult) {
    const completed = completeAcceptedRecordingSubmission(
      recordingSnapshot,
      pendingRecordingSubmissions,
      result,
      submissionFailureFinalPath,
    );
    pendingRecordingSubmissions = completed.pending;
    submissionFailureFinalPath = completed.submissionFailureFinalPath;
    const job = createRecordingJob(result);
    jobs = prependRecordingJob(jobs, job);
    selectedJobId = job.id;
    view = "transcript";
    subscribe(job);
    if (completed.snapshot !== recordingSnapshot) {
      ignoreRecordingEvents = true;
      publishRecordingSnapshot(completed.snapshot);
    }
  }

  function showRecordingFailure(error: unknown) {
    const transition = transitionRecordingSubmissionFailure(
      recordingSnapshot,
      error,
      { ignoreRecordingEvents, submissionFailureFinalPath },
    );
    // 三步都在同一个同步调用栈内完成：先建立路径 marker，再发布本地 failed，
    // 最后解除全量忽略。后续原生事件只能经过 marker-aware merge。
    submissionFailureFinalPath =
      transition.eventGate.submissionFailureFinalPath;
    publishRecordingSnapshot(transition.snapshot);
    ignoreRecordingEvents = transition.eventGate.ignoreRecordingEvents;
    view = "recording";
  }

  async function performFinalization(): Promise<RecordingSubmissionResult> {
    if (!api) throw new Error("转写服务尚未就绪");
    recordingActionPending = true;
    view = "recording";
    try {
      const stopped = await (async () => {
        if (pageMounted) {
          publishRecordingSnapshot({
            ...recordingSnapshot,
            phase: "stopping",
            error: null,
          });
        }
        const stopped = await recordingController.stop();
        if (pageMounted) {
          const stoppedFinalPath = stopped.final_path.trim() || null;
          ignoreRecordingEvents = true;
          publishRecordingSnapshot({
            ...recordingSnapshot,
            phase: "submitting",
            final_path: stoppedFinalPath,
            recoverable_paths: stoppedFinalPath ? [stoppedFinalPath] : [],
            error: null,
          });
        }
        return stopped;
      })();
      const result = await recordingSubmissions.submitAndAccept(
        stopped.final_path,
        api,
        async (accepted) => {
          if (pageMounted) acceptSubmissionResult(accepted);
        },
      );
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
    publishRecordingSnapshot({
      ...recordingSnapshot,
      phase: "submitting",
      error: null,
    });
    try {
      await recordingSubmissions.submitAndAccept(
        finalPath,
        api,
        async (accepted) => {
          if (pageMounted) acceptSubmissionResult(accepted);
        },
      );
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
    submissionFailureFinalPath = null;
    recordingActionPending = true;
    view = "recording";
    publishRecordingSnapshot({
      ...recordingState(),
      phase: "requesting_permissions",
    });
    const revisionBeforeStart = recordingSnapshots.revision;
    try {
      const permissions = await getRecordingPermissions();
      if (!pageMounted) return;
      recordingPermissions = permissions;
      if (permissions.systemAudio === "denied") {
        publishRecordingSnapshot({
          ...recordingState(),
          phase: "failed",
          system_audio: "denied",
          microphone: permissions.microphone === "denied" ? "denied" : "pending",
          error: "需要系统录音权限才能开始录音",
        });
        return;
      }

      const snapshot = await recordingController.start();
      if (pageMounted && recordingSnapshots.revision === revisionBeforeStart) {
        publishRecordingSnapshot(snapshot);
      }
    } catch (error) {
      if (!pageMounted) return;
      try {
        recordingPermissions = await getRecordingPermissions();
      } catch {
        // 保留原始启动错误；权限刷新只是为了决定是否显示设置入口。
      }
      // start Promise 失败是该次启动的权威终态；即使之前已经收到 requesting/starting
      // 事件，也必须发布本地 failed，结束关闭流程的等待。
      showRecordingFailure(error);
    } finally {
      if (pageMounted) recordingActionPending = false;
    }
  }

  function openRecordingEntry() {
    if (recordingSnapshot.final_path || pendingRecordingSubmissions.length > 0) {
      view = "recording";
      return;
    }
    void startDirectRecording();
  }

  async function recoverRecording(recording: RecoverableRecordingItem) {
    if (!api || recording.busy || recordingActive) return;
    recoverableRecordings = beginRecoverableRecording(
      recoverableRecordings,
      recording.sessionId,
    );
    const pendingKey = `recover:${recording.sessionId}`;
    const label = `${recoverableStartedAt(recording)} 的录音`;
    try {
      const mixed = await retryRecordingMix(recording.sessionId);
      if (!pageMounted) return;
      const finalPath = validateFinalPath(mixed.final_path);
      // 混音已经成功并清理了 .incomplete；即使后续提交失败，也不能再展示一个
      // 已不存在的恢复会话；每段最终文件进入独立待提交队列，不能覆盖别段结果。
      recoverableRecordings = completeRecoverableRecording(
        recoverableRecordings,
        recording.sessionId,
      );
      pendingRecordingSubmissions = upsertPendingRecordingSubmission(
        pendingRecordingSubmissions,
        { key: pendingKey, label, finalPath, busy: true, error: null },
      );
      await recordingSubmissions.submitAndAccept(
        finalPath,
        api,
        async (accepted) => {
          if (pageMounted) acceptSubmissionResult(accepted);
        },
      );
      if (!pageMounted) return;
    } catch (error) {
      if (!pageMounted) return;
      if (error instanceof RecordingSubmissionError) {
        pendingRecordingSubmissions = upsertPendingRecordingSubmission(
          pendingRecordingSubmissions,
          {
            key: pendingKey,
            label,
            finalPath: error.finalPath,
            busy: false,
            error: messageOf(error),
          },
        );
      } else {
        recoverableRecordings = failRecoverableRecording(
          recoverableRecordings,
          recording.sessionId,
          `恢复失败：${messageOf(error)}`,
        );
      }
      throw error;
    }
  }

  async function retryPendingRecordingSubmission(
    pending: PendingRecordingSubmission,
  ) {
    if (!api || pending.busy) return;
    pendingRecordingSubmissions = upsertPendingRecordingSubmission(
      pendingRecordingSubmissions,
      { ...pending, busy: true, error: null },
    );
    try {
      await recordingSubmissions.submitAndAccept(
        pending.finalPath,
        api,
        async (accepted) => {
          if (pageMounted) acceptSubmissionResult(accepted);
        },
      );
      if (!pageMounted) return;
    } catch (error) {
      if (pageMounted) {
        pendingRecordingSubmissions = upsertPendingRecordingSubmission(
          pendingRecordingSubmissions,
          { ...pending, busy: false, error: messageOf(error) },
        );
      }
      throw error;
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

  async function submitFinalPathForClose(finalPath: string) {
    if (!api) throw new Error("转写服务尚未就绪");
    publishRecordingSnapshot({
      ...recordingSnapshot,
      phase: "submitting",
      final_path: finalPath,
      recoverable_paths: [finalPath],
      error: null,
    });
    try {
      await recordingSubmissions.submitAndAccept(
        finalPath,
        api,
        async (accepted) => {
          if (pageMounted) acceptSubmissionResult(accepted);
        },
      );
    } catch (error) {
      if (pageMounted) {
        const retained = retainFailedRecordingSubmission(
          recordingSnapshot,
          pendingRecordingSubmissions,
          {
            key: `final:${finalPath}`,
            label: "录音结果",
            finalPath,
            error,
          },
        );
        pendingRecordingSubmissions = retained.pending;
        submissionFailureFinalPath = retained.snapshot.final_path;
        publishRecordingSnapshot(retained.snapshot);
        view = "recording";
      }
      throw error;
    }
  }

  function stopSaveAndClose(): Promise<void> {
    return recordingClose.run(async () => {
      closePending = true;
      let awaitedRevision = recordingSnapshots.revision;
      try {
        await runRecordingCloseFlow(recordingSnapshot, {
          stopAndSubmit: async () => { await finalizeRecordingOnce(); },
          submitFinalPath: submitFinalPathForClose,
          waitForSnapshot: async () => {
            if (
              recordingSnapshot.phase === "submitting" &&
              recordingSnapshot.final_path
            ) {
              const matchingSubmission = recordingSubmissions.getAccepted(
                recordingSnapshot.final_path,
              );
              if (matchingSubmission) {
                await matchingSubmission;
                return recordingSnapshots.snapshot;
              }
            }
            if (finalizationInFlight) {
              await finalizationInFlight;
              return recordingSnapshots.snapshot;
            }
            const snapshot = await recordingSnapshots.waitForAdvance(
              awaitedRevision,
              30_000,
            );
            awaitedRevision = recordingSnapshots.revision;
            return snapshot;
          },
          close: closeAfterRecording,
        });
      } catch (error) {
        // 保存、混音、提交、监听或等待任何一步失败都留在当前窗口。
        if (pageMounted) {
          errorBanner = messageOf(error);
          closeRequested = false;
        }
        throw error;
      } finally {
        if (pageMounted) closePending = false;
      }
    });
  }

  function onRecordingCloseRequested() {
    if (["idle", "ready", "failed"].includes(recordingSnapshot.phase)) {
      // Rust 只应在活跃阶段发该事件；遇到竞态造成的晚到事件时直接走安全关闭命令，
      // 绝不能调用 stop 去停止下一代会话。
      void stopSaveAndClose().catch(() => undefined);
      return;
    }
    if (closePending) return;
    closeRequested = true;
    if (!["starting", "recording"].includes(recordingSnapshot.phase)) {
      // 已在请求权限或收尾时没有“继续录音”可选，直接等待现有流程完成。
      void stopSaveAndClose().catch(() => undefined);
    }
  }

  function recoverableStartedAt(recording: RecoverableRecordingItem): string {
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
        if (!cancelled) {
          recordingSnapshots.fail(error);
          errorBanner = `录音状态监听失败：${messageOf(error)}`;
        }
      },
    );
    const disposeCloseRequested = manageAsyncListener(
      watchRecordingCloseRequested(() => {
        if (!cancelled) onRecordingCloseRequested();
      }),
      (error) => {
        if (!cancelled) errorBanner = `退出保护监听失败：${messageOf(error)}`;
      },
    );

    // 录音状态、权限和遗留会话不依赖 Python 转写服务，启动即并行读取，不能阻塞
    // 现有任务列表和拖放入口。revision guard 避免晚到快照覆盖已收到的实时事件。
    const initialRevision = recordingSnapshots.revision;
    void getRecordingState()
      .then((snapshot) => {
        if (cancelled || recordingSnapshots.revision !== initialRevision) return;
        if (!recordingSnapshots.publishBackend(snapshot, ignoreRecordingEvents)) return;
        recordingSnapshot = recordingSnapshots.snapshot;
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
        if (!cancelled) {
          recoverableRecordings = makeRecoverableRecordingItems(recordings);
        }
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
      recordingSnapshots.cancel("录音页面已销毁");
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
    const normalizedPath = path.trim();
    // 只收音频文件，忽略其他误拖入
    if (normalizedPath && !/\.(m4a|mp3|wav|aac|flac|mp4|mov|ogg)$/i.test(normalizedPath)) {
      errorBanner = `不支持的文件类型（只收音频）：${normalizedPath}`;
      return;
    }
    try {
      await recordingSubmissions.submitAndAccept(
        normalizedPath,
        api,
        async (accepted) => {
          if (pageMounted) acceptSubmissionResult(accepted);
        },
      );
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
      recordingResultPending={Boolean(recordingSnapshot.final_path) || pendingRecordingSubmissions.length > 0}
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
      {#each recoverableRecordings as recovery (recovery.sessionId)}
        <div class="recovery-notice" role="status">
          <span>
            发现一段未完成录音（开始于 {recoverableStartedAt(recovery)}），可尝试恢复。
            {#if recovery.error}<small>{recovery.error}</small>{/if}
          </span>
          <button
            disabled={recovery.busy || recordingActive}
            aria-busy={recovery.busy}
            onclick={() => void recoverRecording(recovery).catch(() => undefined)}
          >{recovery.busy ? "正在恢复…" : "恢复录音"}</button>
        </div>
      {/each}

      {#each pendingRecordingSubmissions as pending (pending.key)}
        <div class="recovery-notice pending-submission" role="status">
          <span>
            {pending.label}已保存到 {pending.finalPath}，等待提交转写。
            {#if pending.error}<small>{pending.error}</small>{/if}
          </span>
          <button
            disabled={pending.busy}
            aria-busy={pending.busy}
            onclick={() => void retryPendingRecordingSubmission(pending).catch(() => undefined)}
          >{pending.busy ? "正在提交…" : "重新提交转写"}</button>
        </div>
      {/each}

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
          <p class="modal-body">
            {#if ["starting", "recording"].includes(recordingSnapshot.phase)}
              关闭前需要先停止并保存录音。保存并提交成功后，whosaid 会自动关闭。
            {:else if recordingSnapshot.phase === "requesting_permissions"}
              正在等待录音权限结果，完成后会继续保存并安全关闭。
            {:else}
              录音正在保存或合成，完成并提交成功后会自动关闭。
            {/if}
          </p>
          <div class="modal-actions">
            {#if ["starting", "recording"].includes(recordingSnapshot.phase)}
              <button
                class="btn-cancel"
                disabled={closePending}
                onclick={() => (closeRequested = false)}
              >继续录音</button>
            {/if}
            <button
              class="btn-danger"
              disabled={closePending}
              aria-busy={closePending}
              onclick={() => void stopSaveAndClose().catch(() => undefined)}
            >{closePending
                ? ["starting", "recording"].includes(recordingSnapshot.phase)
                  ? "正在停止并保存…"
                  : "等待保存完成…"
                : ["starting", "recording"].includes(recordingSnapshot.phase)
                  ? "停止并保存"
                  : "等待保存完成"}</button>
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
  .recovery-notice span { min-width: 0; overflow-wrap: anywhere; }
  .recovery-notice small {
    display: block;
    margin-top: 3px;
    color: var(--danger);
  }
  .pending-submission {
    border-color: color-mix(in srgb, var(--accent) 35%, var(--hairline));
    background: color-mix(in srgb, var(--accent) 8%, var(--card));
  }
  .pending-submission button {
    border-color: var(--accent);
    color: var(--accent);
  }

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
