import type { FramePurpose, Frame } from "./contracts";

const MAX_SIDE = 768;
const QUALITY = 0.8;

export function newId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `f-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/** Rear camera with on-demand JPEG capture (max side 768 px, quality 0.8). */
export class Camera {
  private stream: MediaStream | null = null;
  private starting: Promise<void> | null = null;

  constructor(private readonly video: HTMLVideoElement) {
    video.muted = true;
    video.playsInline = true;
    video.setAttribute("playsinline", "");
  }

  get ready(): boolean {
    return this.stream !== null && this.video.videoWidth > 0;
  }

  start(): Promise<void> {
    if (this.stream) return Promise.resolve();
    if (!this.starting) {
      this.starting = this.init().finally(() => {
        this.starting = null;
      });
    }
    return this.starting;
  }

  private async init(): Promise<void> {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("Camera needs HTTPS");
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: {
        facingMode: { ideal: "environment" },
        width: { ideal: 1280 },
        height: { ideal: 960 },
      },
    });
    this.stream = stream;
    this.video.srcObject = stream;
    stream.getVideoTracks()[0]?.addEventListener("ended", () => {
      this.stream = null;
    });
    await this.video.play().catch(() => undefined);
    await this.waitForSize();
  }

  private waitForSize(): Promise<void> {
    if (this.video.videoWidth > 0) return Promise.resolve();
    return new Promise((resolve) => {
      const done = (): void => {
        this.video.removeEventListener("loadedmetadata", done);
        resolve();
      };
      this.video.addEventListener("loadedmetadata", done);
      window.setTimeout(done, 3000);
    });
  }

  /** Capture a frame message. Starts the camera if needed. */
  async capture(purpose: FramePurpose, requestId: string | null): Promise<Frame> {
    await this.start();
    await this.waitForSize();
    const vw = this.video.videoWidth;
    const vh = this.video.videoHeight;
    if (!vw || !vh) throw new Error("Camera has no picture yet");
    const scale = Math.min(1, MAX_SIDE / Math.max(vw, vh));
    const w = Math.round(vw * scale);
    const h = Math.round(vh * scale);
    const jpeg = await encodeJpeg(this.video, w, h);
    return { type: "frame", id: newId(), jpeg, w, h, purpose, request_id: requestId };
  }
}

async function encodeJpeg(src: HTMLVideoElement, w: number, h: number): Promise<string> {
  if (typeof OffscreenCanvas !== "undefined") {
    try {
      const oc = new OffscreenCanvas(w, h);
      const octx = oc.getContext("2d");
      if (octx) {
        octx.drawImage(src, 0, 0, w, h);
        const blob = await oc.convertToBlob({ type: "image/jpeg", quality: QUALITY });
        return await blobToBase64(blob);
      }
    } catch {
      /* fall back to a DOM canvas (older Safari) */
    }
  }
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const c2d = canvas.getContext("2d");
  if (!c2d) throw new Error("Canvas unavailable");
  c2d.drawImage(src, 0, 0, w, h);
  const url = canvas.toDataURL("image/jpeg", QUALITY);
  return url.slice(url.indexOf(",") + 1);
}

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => {
      const s = String(r.result);
      resolve(s.slice(s.indexOf(",") + 1));
    };
    r.onerror = () => reject(r.error ?? new Error("read failed"));
    r.readAsDataURL(blob);
  });
}
