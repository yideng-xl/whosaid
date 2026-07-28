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
});
