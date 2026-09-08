export interface AudioLevel {
  source: "system" | "microphone";
  peak: number;
  sampledAt: number;
}

export const WAVEFORM_SAMPLES = 60;
export const LEVEL_MAX_AGE_MS = 500;

export function validLevel(value: unknown, source: AudioLevel["source"], since: number, now: number): value is AudioLevel {
  if (!value || typeof value !== "object") return false;
  const level = value as AudioLevel;
  return level.source === source && Number.isFinite(level.peak) && level.peak >= 0 && level.peak <= 1 &&
    Number.isFinite(level.sampledAt) && level.sampledAt >= since &&
    level.sampledAt <= now + 100 && now - level.sampledAt <= LEVEL_MAX_AGE_MS;
}

/** 固定 -60 至 0 dBFS 范围，不自动放大底噪；零输入保持平线。 */
export function waveformHeight(peak: number): number {
  if (!Number.isFinite(peak) || peak <= 0.001) return 0;
  return Math.min(1, Math.max(0, (20 * Math.log10(peak) + 60) / 60));
}

export function appendWaveform(history: number[], peak: number): number[] {
  return [...history, waveformHeight(peak)].slice(-WAVEFORM_SAMPLES);
}
