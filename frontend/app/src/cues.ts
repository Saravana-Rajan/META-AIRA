import { audioContext } from "./audio";
import type { Cue } from "./contracts";

/** Earcons + haptics. Each cue is a short, distinct pattern so it can be learned by ear. */
export type LocalCue = Cue | "talk_on" | "talk_off" | "stop" | "connected" | "disconnected" | "confirm";

type Tone = [freqHz: number, ms: number, gapMs?: number];

const TONES: Record<LocalCue, Tone[]> = {
  ack: [[880, 70]],
  thinking: [[520, 60, 90], [520, 60]],
  done: [[660, 80, 20], [990, 120]],
  warn: [[330, 180, 60], [330, 180]],
  match: [[784, 90, 20], [1046, 90, 20], [1318, 140]],
  mismatch: [[620, 120, 30], [440, 120, 30], [300, 200]],
  unsure: [[600, 90, 40], [700, 90, 40], [600, 90]],
  talk_on: [[700, 60, 20], [1050, 90]],
  talk_off: [[1050, 60, 20], [700, 90]],
  stop: [[260, 220]],
  connected: [[1200, 50]],
  disconnected: [[400, 120, 40], [300, 160]],
  confirm: [[880, 90, 60], [880, 90]],
};

const HAPTICS: Record<LocalCue, number[]> = {
  ack: [30],
  thinking: [15, 80, 15],
  done: [40, 40, 80],
  warn: [200, 80, 200],
  match: [40, 40, 40, 40, 120],
  mismatch: [250, 100, 250],
  unsure: [60, 60, 60, 60, 60],
  talk_on: [50],
  talk_off: [25, 50, 25],
  stop: [300],
  connected: [20],
  disconnected: [100, 50, 100],
  confirm: [80, 60, 80],
};

export function cue(name: LocalCue): void {
  vibrate(HAPTICS[name]);
  beep(TONES[name]);
}

export function vibrate(pattern: number[]): void {
  try {
    navigator.vibrate?.(pattern);
  } catch {
    /* unsupported (iOS) */
  }
}

function beep(seq: Tone[]): void {
  const ctx = audioContext();
  if (!ctx || ctx.state !== "running") return;
  let t = ctx.currentTime + 0.01;
  for (const [freq, ms, gap = 0] of seq) {
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = freq;
    const dur = ms / 1000;
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(0.25, t + 0.01);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    osc.connect(g).connect(ctx.destination);
    osc.start(t);
    osc.stop(t + dur + 0.02);
    t += dur + gap / 1000;
  }
}
