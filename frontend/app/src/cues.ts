import { audioContext } from "./audio";
import type { Cue } from "./contracts";

/** Earcons + haptics. Each cue is a short, word-free pattern (<= 600 ms) that can be learned by ear. */
export type LocalCue = Cue | "talk_on" | "talk_off" | "stop" | "connected" | "disconnected" | "confirm" | "working";

/** [frequency Hz (0 = rest), duration ms] */
type Note = readonly [number, number];
interface Sound {
  notes: readonly Note[];
  /** peak gain, 0..1 */
  vol: number;
}

const SOUNDS: Record<LocalCue, Sound> = {
  // server cues: `match` climbs and `mismatch` drops, so the two never need words
  ack: { notes: [[880, 60]], vol: 0.25 },
  thinking: { notes: [[440, 120], [494, 120]], vol: 0.08 },
  done: { notes: [[660, 80], [990, 120]], vol: 0.25 },
  warn: { notes: [[330, 150], [330, 150]], vol: 0.35 },
  match: { notes: [[523, 90], [659, 90], [784, 140]], vol: 0.3 },
  mismatch: { notes: [[784, 140], [392, 260]], vol: 0.35 },
  unsure: { notes: [[587, 120], [587, 120]], vol: 0.25 },
  // local cues
  talk_on: { notes: [[587, 70], [880, 110]], vol: 0.2 },
  talk_off: { notes: [[880, 60], [587, 90]], vol: 0.15 },
  stop: { notes: [[700, 40]], vol: 0.15 },
  connected: { notes: [[523, 80], [784, 80]], vol: 0.2 },
  disconnected: { notes: [[392, 200], [262, 300]], vol: 0.3 },
  confirm: { notes: [[880, 90], [0, 60], [880, 90]], vol: 0.25 },
  working: { notes: [[392, 50], [0, 70], [392, 50]], vol: 0.07 },
};

const BUZZ: Record<LocalCue, number[]> = {
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
  working: [],
};

export function cue(name: LocalCue): void {
  vibrate(BUZZ[name]);
  play(SOUNDS[name]);
}

export function vibrate(pattern: number[]): void {
  if (pattern.length === 0) return;
  try {
    navigator.vibrate?.(pattern);
  } catch {
    /* unsupported (iOS) */
  }
}

function play(sound: Sound): void {
  const ctx = audioContext();
  if (!ctx || ctx.state !== "running") return;
  let at = ctx.currentTime + 0.01;
  for (const [hz, ms] of sound.notes) {
    const len = ms / 1000;
    if (hz > 0) {
      const osc = ctx.createOscillator();
      const amp = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = hz;
      amp.gain.setValueAtTime(0, at);
      amp.gain.linearRampToValueAtTime(sound.vol, at + 0.01);
      amp.gain.linearRampToValueAtTime(0, at + len);
      osc.connect(amp).connect(ctx.destination);
      osc.start(at);
      osc.stop(at + len + 0.02);
    }
    at += len;
  }
}
