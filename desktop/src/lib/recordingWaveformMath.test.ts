import { describe, expect, it } from "vitest";
import { appendWaveform, validLevel, waveformHeight } from "./recordingWaveform";

describe("波形采样", () => {
  it("固定分贝范围，静音和无效输入保持平线", () => {
    for (const peak of [0, -1, NaN, Infinity, 0.0001]) expect(waveformHeight(peak)).toBe(0);
    expect(waveformHeight(1)).toBe(1);
    expect(waveformHeight(0.01)).toBeCloseTo(1 / 3);
  });
  it("历史长度有界", () => {
    let history: number[] = [];
    for (let i = 0; i < 10000; i++) history = appendWaveform(history, 0.1);
    expect(history).toHaveLength(60);
    expect(appendWaveform(history, 0).at(-1)).toBe(0);
  });
  it("拒绝其他来源、上一段、过期和非法峰值", () => {
    const level = { source: "system", peak: 0.5, sampledAt: 1100 };
    expect(validLevel(level, "system", 1000, 1200)).toBe(true);
    expect(validLevel(level, "microphone", 1000, 1200)).toBe(false);
    expect(validLevel(level, "system", 1200, 1200)).toBe(false);
    expect(validLevel(level, "system", 1000, 1800)).toBe(false);
    expect(validLevel({ ...level, peak: NaN }, "system", 1000, 1200)).toBe(false);
    expect(validLevel({ ...level, peak: 2 }, "system", 1000, 1200)).toBe(false);
    expect(validLevel(null, "system", 1000, 1200)).toBe(false);
  });
});
