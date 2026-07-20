import { describe, it, expect } from "vitest";
import { hasUndownloadedActiveModel } from "./modelState";
import type { ModelInfo } from "./api";

function model(overrides: Partial<ModelInfo>): ModelInfo {
  return {
    id: "m1", kind: "transcribe", display_name: "M1",
    downloaded: true, active: false, size_mb: 100,
    ...overrides,
  };
}

describe("hasUndownloadedActiveModel", () => {
  it("returns true when the active model is not downloaded", () => {
    const models = [model({ active: true, downloaded: false })];
    expect(hasUndownloadedActiveModel(models)).toBe(true);
  });

  it("returns false when the active model is downloaded", () => {
    const models = [model({ active: true, downloaded: true })];
    expect(hasUndownloadedActiveModel(models)).toBe(false);
  });

  it("returns false for an empty list", () => {
    expect(hasUndownloadedActiveModel([])).toBe(false);
  });

  it("ignores non-active undownloaded models", () => {
    const models = [model({ active: false, downloaded: false })];
    expect(hasUndownloadedActiveModel(models)).toBe(false);
  });
});
