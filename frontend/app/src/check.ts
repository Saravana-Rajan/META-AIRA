import { audioContext } from "./audio";
import type { Camera } from "./camera";
import type { Strings } from "./i18n";

/**
 * Start-up device check, done entirely on the phone: nothing is sent to the server.
 *  - microphone: opens the mic and waits for a voice-level sound ("Say: Hello AIRA");
 *  - camera: starts the rear camera and checks the picture is not black.
 * Each step is spoken (through `speak`) and shown as a card with a status pill.
 */
export type MicResult = "ok" | "blocked" | "silent";
export type CamResult = "ok" | "blocked" | "dark";
type StepState = "waiting" | "checking" | "passed" | "failed";

const sleep = (ms: number): Promise<void> => new Promise((r) => window.setTimeout(r, ms));

export async function listenForVoice(onLevel: (lv: number) => void, timeoutMs = 7000): Promise<MicResult> {
  const ctx = audioContext();
  if (!ctx || !navigator.mediaDevices?.getUserMedia) return "blocked";
  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true }, video: false });
  } catch {
    return "blocked";
  }
  if (ctx.state !== "running") await ctx.resume().catch(() => undefined);
  const src = ctx.createMediaStreamSource(stream);
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  src.connect(analyser);
  const buf = new Float32Array(analyser.fftSize);
  const until = performance.now() + timeoutMs;
  let loudMs = 0;
  let last = performance.now();
  try {
    while (performance.now() < until) {
      await sleep(50);
      analyser.getFloatTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += (buf[i] ?? 0) ** 2;
      const lv = Math.min(1, Math.sqrt(sum / buf.length) * 8);
      onLevel(lv);
      const now = performance.now();
      if (lv > 0.12) loudMs += now - last;
      last = now;
      if (loudMs >= 250) return "ok";
    }
    return "silent";
  } finally {
    onLevel(0);
    src.disconnect();
    stream.getTracks().forEach((t) => t.stop());
  }
}

/** Mean brightness (0..255) of a small copy of the current video frame. */
function brightness(video: HTMLVideoElement): number {
  const c = document.createElement("canvas");
  c.width = 32;
  c.height = 24;
  const g = c.getContext("2d", { willReadFrequently: true });
  if (!g || !video.videoWidth) return 0;
  g.drawImage(video, 0, 0, c.width, c.height);
  const px = g.getImageData(0, 0, c.width, c.height).data;
  let sum = 0;
  for (let i = 0; i < px.length; i += 4) sum += 0.299 * (px[i] ?? 0) + 0.587 * (px[i + 1] ?? 0) + 0.114 * (px[i + 2] ?? 0);
  return sum / (px.length / 4);
}

export async function lookThroughCamera(camera: Camera, video: HTMLVideoElement, peek: HTMLElement): Promise<CamResult> {
  try {
    await camera.start();
  } catch {
    return "blocked";
  }
  // A second <video> on the same stream for the preview; AIRA's own element stays where it is.
  if (!peek.querySelector("video") && video.srcObject) {
    const pv = document.createElement("video");
    pv.muted = true;
    pv.playsInline = true;
    pv.setAttribute("playsinline", "");
    pv.srcObject = video.srcObject;
    peek.replaceChildren(pv);
    void pv.play().catch(() => undefined);
  }
  await sleep(1200); // let exposure settle
  if (!video.videoWidth) return "blocked";
  return brightness(video) < 18 ? "dark" : "ok";
}

export interface CheckDeps {
  camera: Camera;
  video: HTMLVideoElement;
  t: () => Strings;
  lang: () => string;
  /** Speak a line; resolves when it has finished (so the mic opens after AIRA stops talking). */
  speak: (text: string) => Promise<void>;
  onDone: () => void;
}

function el<T extends HTMLElement = HTMLElement>(id: string): T {
  const e = document.getElementById(id);
  if (!e) throw new Error(`#${id} missing`);
  return e as T;
}

/** Drives the #check screen. */
export class DeviceCheck {
  private running = false;
  private finished = false;
  private readonly line = el("check-line");
  private readonly meter = el("mic-meter");
  private readonly retry = el<HTMLButtonElement>("check-retry");
  private readonly go = el<HTMLButtonElement>("check-go");
  private readonly skip = el<HTMLButtonElement>("check-skip");

  constructor(private readonly d: CheckDeps) {
    this.retry.addEventListener("click", () => void this.run());
    this.go.addEventListener("click", () => this.finish());
    this.skip.addEventListener("click", () => this.finish());
  }

  render(): void {
    const t = this.d.t();
    el("check-title").textContent = t.checkTitle;
    el("step-mic-name").textContent = t.micStep;
    el("step-cam-name").textContent = t.camStep;
    this.retry.textContent = t.tryAgain;
    el("check-go-label").textContent = t.startTalking;
    el("check-go-hint").textContent = t.hint;
    this.skip.textContent = t.skipCheck;
    for (const id of ["step-mic", "step-cam"]) this.step(id, (el(id).dataset.step as StepState) ?? "waiting");
  }

  private step(id: "step-mic" | "step-cam" | string, s: StepState, detail?: string): void {
    const t = this.d.t();
    el(id).dataset.step = s;
    el(`${id}-pill`).textContent = s === "waiting" ? t.waiting : s === "checking" ? t.checking : s === "passed" ? t.works : t.needsAttention;
    if (detail !== undefined) el(`${id}-detail`).textContent = detail;
  }

  private say(text: string): Promise<void> {
    this.line.textContent = text;
    return this.d.speak(text);
  }

  async run(): Promise<void> {
    if (this.running || this.finished) return;
    this.running = true;
    const t = this.d.t();
    this.retry.hidden = true;
    this.go.hidden = true;
    this.skip.hidden = false;
    this.step("step-mic", "waiting", "");
    this.step("step-cam", "waiting", "");

    this.step("step-mic", "checking");
    await this.say(t.micSay);
    if (this.finished) return;
    const mic = await listenForVoice((lv) => {
      this.meter.style.width = `${Math.round(lv * 100)}%`;
    });
    if (this.finished) return;
    const micOk = mic === "ok";
    this.step("step-mic", micOk ? "passed" : "failed", micOk ? t.micOk : mic === "blocked" ? t.micBlocked : t.micSilent);

    this.step("step-cam", "checking");
    await this.say(t.camLook);
    if (this.finished) return;
    const cam = await lookThroughCamera(this.d.camera, this.d.video, el("cam-peek"));
    if (this.finished) return;
    const camOk = cam === "ok";
    this.step("step-cam", camOk ? "passed" : "failed", camOk ? t.camOk : cam === "blocked" ? t.camBlocked : t.camDark);

    this.running = false;
    if (micOk && camOk) {
      this.go.hidden = false;
      this.skip.hidden = true;
      this.go.focus();
      await this.say(t.allGood);
    } else {
      this.retry.hidden = false;
      this.retry.focus();
      await this.say(t.someFailed);
    }
  }

  private finish(): void {
    if (this.finished) return;
    this.finished = true;
    this.running = false;
    el("cam-peek").replaceChildren();
    this.d.onDone();
  }
}
