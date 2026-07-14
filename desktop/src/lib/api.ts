// 前端 API 客户端：REST + WebSocket 双协议

export interface JobSummary {
  id: string;
  status: string;
  progress: number;
  error: string | null;
  audio_path: string;
}

export interface ModelInfo {
  id: string;
  kind: string;
  display_name: string;
  downloaded: boolean;
  active: boolean;
  size_mb: number;
}

interface ProgressMessage {
  status: string;
  progress: number;
  error: string | null;
}

export function createApi(port: number) {
  const base = `http://localhost:${port}`;

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

    async submitJob(audioPath: string): Promise<string> {
      const r = await fetch(`${base}/jobs`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ audio_path: audioPath }),
      });
      return (await j(r)).job_id;
    },

    async getJob(id: string) {
      return j(await fetch(`${base}/jobs/${id}`));
    },

    async rename(id: string, orig: string, name: string): Promise<void> {
      await fetch(`${base}/jobs/${id}/rename`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ orig, name }),
      });
    },

    exportUrl(id: string, fmt: "txt" | "srt"): string {
      return `${base}/jobs/${id}/export?fmt=${fmt}`;
    },

    // 模型 API
    async listModels(): Promise<ModelInfo[]> {
      return j(await fetch(`${base}/models`));
    },

    async downloadModel(id: string): Promise<void> {
      await fetch(`${base}/models/${id}/download`, { method: "POST" });
    },

    async setActive(id: string): Promise<void> {
      await fetch(`${base}/models/active`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ model_id: id }),
      });
    },

    // WebSocket 进度订阅
    watchProgress(
      id: string,
      onMsg: (m: ProgressMessage) => void
    ): () => void {
      const ws = new WebSocket(`ws://localhost:${port}/ws/jobs/${id}`);
      ws.onmessage = (e) => onMsg(JSON.parse(e.data));
      return () => ws.close();
    },
  };
}
