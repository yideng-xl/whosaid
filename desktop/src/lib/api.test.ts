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
});
