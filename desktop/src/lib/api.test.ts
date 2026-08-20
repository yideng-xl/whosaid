import { describe, it, expect, vi } from "vitest";
import { createApi } from "./api";

describe("api", () => {
  it("submitJob POSTs audio_path and returns job_id", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ job_id: "job1" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const api = createApi(12345);
    const id = await api.submitJob("/x/a.m4a");
    expect(id).toBe("job1");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:12345/jobs",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("submitJob 可携带录音幂等键", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ job_id: "job1" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const api = createApi(12345);
    await api.submitJob("/recordings/a.m4a", undefined, "recording:req-1");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:12345/jobs",
      expect.objectContaining({
        body: JSON.stringify({
          audio_path: "/recordings/a.m4a",
          num_speakers: null,
          idempotency_key: "recording:req-1",
        }),
      }),
    );
  });

  it("exportUrl builds correct url", () => {
    const api = createApi(999);
    expect(api.exportUrl("job2", "srt")).toBe(
      "http://127.0.0.1:999/jobs/job2/export?fmt=srt");
  });

  it("exportUrl 支持 plain 逐字稿", () => {
    const api = createApi(999);
    expect(api.exportUrl("job2", "plain")).toContain("fmt=plain");
  });

  it("pauseJob POSTs to pause endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal("fetch", fetchMock);
    const api = createApi(2222);
    await api.pauseJob("job7");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:2222/jobs/job7/pause",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("resumeJob POSTs to resume endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal("fetch", fetchMock);
    const api = createApi(2222);
    await api.resumeJob("job7");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:2222/jobs/job7/resume",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("deleteJob DELETEs job endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal("fetch", fetchMock);
    const api = createApi(2222);
    await api.deleteJob("job7");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:2222/jobs/job7",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("speakerSampleUrl encodes spk", () => {
    const api = createApi(3333);
    expect(api.speakerSampleUrl("j1", "说话人A")).toBe(
      "http://127.0.0.1:3333/jobs/j1/speaker_sample?spk=" + encodeURIComponent("说话人A"));
  });

  it("setNumSpeakers POSTs num_speakers", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal("fetch", fetchMock);
    const api = createApi(2222);
    await api.setNumSpeakers("job7", 3);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:2222/jobs/job7/num_speakers",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ num_speakers: 3 }) }),
    );
  });

  it("rediarize POSTs num_speakers (null 允许)", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal("fetch", fetchMock);
    const api = createApi(2222);
    await api.rediarize("job7", null);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:2222/jobs/job7/rediarize",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ num_speakers: null }) }),
    );
  });

  it("replaceTerms POSTs mapping and returns replacement count", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ok: true, replaced: 4 }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const api = createApi(2222);

    const replaced = await api.replaceTerms("job7", { 张山: "张三" });

    expect(replaced).toBe(4);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:2222/jobs/job7/replace_terms",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ mapping: { 张山: "张三" } }),
      }),
    );
  });

  it("getHfSettings fetches settings", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ hf_token: "hf_x", hf_endpoint: null }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const api = createApi(4444);
    const s = await api.getHfSettings();
    expect(s).toEqual({ hf_token: "hf_x", hf_endpoint: null });
    expect(fetchMock).toHaveBeenCalledWith("http://127.0.0.1:4444/settings/hf");
  });

  it("deleteModel DELETEs model endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal("fetch", fetchMock);
    const api = createApi(2222);
    await api.deleteModel("whisper-small");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:2222/models/whisper-small",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("getModelProgress fetches progress endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ downloaded_bytes: 100, total_bytes: 200, percent: 50 }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const api = createApi(2222);
    const p = await api.getModelProgress("whisper-small");
    expect(p).toEqual({ downloaded_bytes: 100, total_bytes: 200, percent: 50 });
    expect(fetchMock).toHaveBeenCalledWith("http://127.0.0.1:2222/models/whisper-small/progress");
  });

  it("setHfSettings POSTs token and endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal("fetch", fetchMock);
    const api = createApi(4444);
    await api.setHfSettings({ hf_token: "hf_x", hf_endpoint: "https://hf-mirror.com" });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:4444/settings/hf",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ hf_token: "hf_x", hf_endpoint: "https://hf-mirror.com" }),
      }),
    );
  });

  it("词库 API 支持查询、添加、编辑和删除", async () => {
    const entry = {
      id: "entry-1", kind: "person", canonical: "许磊", aliases: ["许雷"],
      enabled: true, created_at: 1, updated_at: 1,
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => [entry] })
      .mockResolvedValueOnce({ ok: true, json: async () => entry })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ ...entry, enabled: false }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal("fetch", fetchMock);
    const api = createApi(4444);
    const input = { kind: "person" as const, canonical: "许磊", aliases: ["许雷"], enabled: true };

    expect(await api.listVocabulary()).toEqual([entry]);
    await api.addVocabulary(input);
    await api.updateVocabulary("entry-1", { ...input, enabled: false });
    await api.deleteVocabulary("entry-1");

    expect(fetchMock).toHaveBeenNthCalledWith(1, "http://127.0.0.1:4444/vocabulary");
    expect(fetchMock).toHaveBeenNthCalledWith(2, "http://127.0.0.1:4444/vocabulary", expect.objectContaining({
      method: "POST", body: JSON.stringify(input),
    }));
    expect(fetchMock).toHaveBeenNthCalledWith(3, "http://127.0.0.1:4444/vocabulary/entry-1", expect.objectContaining({
      method: "PUT", body: JSON.stringify({ ...input, enabled: false }),
    }));
    expect(fetchMock).toHaveBeenNthCalledWith(4, "http://127.0.0.1:4444/vocabulary/entry-1", expect.objectContaining({ method: "DELETE" }));
  });

  it("词库 API 支持三个文本框整组保存", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => [] });
    vi.stubGlobal("fetch", fetchMock);
    const api = createApi(4444);
    const groups = {
      person: ["许磊", "张三"], term: ["端到端探测"], other: ["陕西省调"],
    };
    await api.replaceVocabulary(groups);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:4444/vocabulary/bulk",
      expect.objectContaining({ method: "PUT", body: JSON.stringify(groups) }),
    );
  });
});
