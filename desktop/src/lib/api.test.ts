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
});
