/** One shared AudioContext (mic worklet + earcons). Must be created/resumed inside a user gesture on iOS. */
let ctx: AudioContext | null = null;

type AudioContextCtor = typeof AudioContext;

export function audioContext(): AudioContext | null {
  if (ctx) return ctx;
  const Ctor: AudioContextCtor | undefined =
    window.AudioContext ?? (window as unknown as { webkitAudioContext?: AudioContextCtor }).webkitAudioContext;
  if (!Ctor) return null;
  ctx = new Ctor({ latencyHint: "interactive" });
  return ctx;
}

/** Call from a user gesture handler: creates and resumes the context. */
export function unlockAudio(): void {
  const c = audioContext();
  if (c && c.state !== "running") void c.resume().catch(() => undefined);
}

export function bytesToBase64(bytes: Uint8Array): string {
  let bin = "";
  const step = 0x8000;
  for (let i = 0; i < bytes.length; i += step) {
    bin += String.fromCharCode.apply(null, Array.from(bytes.subarray(i, i + step)));
  }
  return btoa(bin);
}
