// 前端 API 客户端：REST + WebSocket 双协议

export interface JobSummary {
  id: string;
  status: string;
  progress: number;
  error: string | null;
  audio_path: string;
  created_at: number; // 拖入/提交时刻（epoch 秒），用于按时间分组倒序
}

export interface ModelInfo {
  id: string;
  kind: string;
  display_name: string;
  downloaded: boolean;
  active: boolean;
  size_mb: number;
}

export interface ModelProgress {
  downloaded_bytes: number;
  total_bytes: number;
  percent: number; // 下载中封顶 99，避免估算值提前跳满 100（真正完成以 downloadModel 的 promise resolve 为准）
}

export interface HfSettings {
  hf_token: string | null;
  hf_endpoint: string | null;
}

export interface Speaker {
  orig: string; // 原始标签，如 "说话人A"，rename 时作为 orig 传回
  name: string; // 当前显示名（改名后为真名）
}

export interface NameCandidate {
  term: string;
  count: number;
}

export interface JobDetail {
  id: string;
  status: string;
  progress: number;
  error: string | null;
  txt: string;
  plain_txt: string; // 纯文字稿（不分说话人），始终为 transcript.plain_text()，与 txt 语义独立
  speakers: Speaker[];
  total_chunks: number;
  chunks_done: number;
  phase: string;
  num_speakers: number | null;
  name_candidates: NameCandidate[];
}

interface ProgressMessage {
  status: string;
  progress: number;
  error: string | null;
}

export function createApi(port: number) {
  // 用 127.0.0.1 而非 localhost：服务由 uvicorn 绑在 127.0.0.1(IPv4)，
  // 而 macOS 上 localhost 常先解析到 IPv6 ::1，导致 webview fetch 报 "Load failed"。
  const host = `127.0.0.1:${port}`;
  const base = `http://${host}`;

  // 辅助方法：统一错误处理和 JSON 解析
  const j = async (r: Response) => {
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  };

  return {
    // 任务 API
    async listJobs(): Promise<JobSummary[]> {
      return j(await fetch(`${base}/jobs`));
    },

    async submitJob(audioPath: string, numSpeakers?: number): Promise<string> {
      const r = await fetch(`${base}/jobs`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ audio_path: audioPath, num_speakers: numSpeakers ?? null }),
      });
      return (await j(r)).job_id;
    },

    async getJob(id: string): Promise<JobDetail> {
      return j(await fetch(`${base}/jobs/${id}`));
    },

    async rename(id: string, orig: string, name: string): Promise<void> {
      // 走 j() 包装：非 2xx（如转写未完成 409）会抛，调用方能感知失败
      await j(await fetch(`${base}/jobs/${id}/rename`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ orig, name }),
      }));
    },

    async replaceTerms(id: string, mapping: Record<string, string>): Promise<number> {
      const result = await j(await fetch(`${base}/jobs/${id}/replace_terms`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ mapping }),
      }));
      return result.replaced;
    },

    exportUrl(id: string, fmt: "txt" | "srt" | "plain"): string {
      return `${base}/jobs/${id}/export?fmt=${fmt}`;
    },

    async pauseJob(id: string): Promise<void> {
      await j(await fetch(`${base}/jobs/${id}/pause`, { method: "POST" }));
    },

    async resumeJob(id: string): Promise<void> {
      await j(await fetch(`${base}/jobs/${id}/resume`, { method: "POST" }));
    },

    async deleteJob(id: string): Promise<void> {
      await j(await fetch(`${base}/jobs/${id}`, { method: "DELETE" }));
    },

    async setNumSpeakers(id: string, n: number | null): Promise<void> {
      await j(await fetch(`${base}/jobs/${id}/num_speakers`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ num_speakers: n }),
      }));
    },

    async rediarize(id: string, n: number | null): Promise<void> {
      await j(await fetch(`${base}/jobs/${id}/rediarize`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ num_speakers: n }),
      }));
    },

    speakerSampleUrl(id: string, spk: string): string {
      return `${base}/jobs/${id}/speaker_sample?spk=${encodeURIComponent(spk)}`;
    },

    // 模型 API
    async listModels(): Promise<ModelInfo[]> {
      return j(await fetch(`${base}/models`));
    },

    async downloadModel(id: string): Promise<void> {
      // 走 j()：下载失败（模型不存在等）能抛给调用方，不被静默吞掉
      await j(await fetch(`${base}/models/${id}/download`, { method: "POST" }));
    },

    async getModelProgress(id: string): Promise<ModelProgress> {
      // 只读轮询端点：下载中不断量该模型 HF 缓存目录当前大小，供前端画进度条；
      // 不走 j() 之外的特殊处理，非 2xx（如未知 model_id）同样按现有约定抛给调用方。
      return j(await fetch(`${base}/models/${id}/progress`));
    },

    async deleteModel(id: string): Promise<void> {
      // 走 j()：删除失败（如缓存目录被占用等）能抛给调用方，不被静默吞掉
      await j(await fetch(`${base}/models/${id}`, { method: "DELETE" }));
    },

    async setActive(id: string): Promise<void> {
      await j(await fetch(`${base}/models/active`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ model_id: id }),
      }));
    },

    async getHfSettings(): Promise<HfSettings> {
      return j(await fetch(`${base}/settings/hf`));
    },

    async setHfSettings(settings: HfSettings): Promise<void> {
      await j(await fetch(`${base}/settings/hf`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(settings),
      }));
    },

    // WebSocket 进度订阅
    watchProgress(
      id: string,
      onMsg: (m: ProgressMessage) => void
    ): () => void {
      const ws = new WebSocket(`ws://${host}/ws/jobs/${id}`);
      ws.onmessage = (e) => onMsg(JSON.parse(e.data));
      // TODO(Task 9)：onerror/onclose 兜底——网络中断或服务重启时通知前端重连/刷新
      ws.onerror = () => console.warn(`[api] WS 进度连接异常 job=${id}`);
      return () => ws.close();
    },
  };
}
