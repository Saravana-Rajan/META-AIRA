import { audioContext, bytesToBase64 } from "./audio";

/**
 * Microphone -> AudioWorklet -> 24 kHz mono PCM16 LE, 80 ms (3840-byte) chunks as base64.
 * The track stays open once granted (re-prompting on iOS is slow); streaming is gated in the worklet.
 */
export class Mic {
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private starting: Promise<void> | null = null;
  private streaming = false;

  constructor(private readonly onChunk: (pcm24b64: string) => void) {}

  get ready(): boolean {
    return this.node !== null;
  }

  /** Acquire the mic and build the graph. Safe to call repeatedly. */
  start(): Promise<void> {
    if (this.node) return Promise.resolve();
    if (!this.starting) {
      this.starting = this.init().finally(() => {
        this.starting = null;
      });
    }
    return this.starting;
  }

  private async init(): Promise<void> {
    const ctx = audioContext();
    if (!ctx) throw new Error("Web Audio is not supported");
    if (!ctx.audioWorklet) throw new Error("AudioWorklet is not supported (needs HTTPS)");
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("Microphone needs HTTPS");
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
      video: false,
    });
    await ctx.audioWorklet.addModule(`${import.meta.env.BASE_URL}pcm-worklet.js`);
    if (ctx.state !== "running") await ctx.resume().catch(() => undefined);
    const source = ctx.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(ctx, "pcm24", {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      outputChannelCount: [1],
    });
    node.port.onmessage = (e: MessageEvent) => {
      if (!this.streaming || !(e.data instanceof ArrayBuffer)) return;
      this.onChunk(bytesToBase64(new Uint8Array(e.data)));
    };
    // Some engines only pull a node that reaches the destination: route through a muted gain.
    const sink = ctx.createGain();
    sink.gain.value = 0;
    source.connect(node);
    node.connect(sink);
    sink.connect(ctx.destination);
    this.stream = stream;
    this.source = source;
    this.node = node;
    this.node.port.postMessage({ enabled: this.streaming });
    stream.getAudioTracks()[0]?.addEventListener("ended", () => this.teardown());
  }

  /** Gate audio streaming on/off (only stream while talking and while AIRA is silent). */
  setStreaming(on: boolean): void {
    if (on === this.streaming) return;
    this.streaming = on;
    this.node?.port.postMessage({ enabled: on });
  }

  private teardown(): void {
    this.source?.disconnect();
    this.node?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    this.source = null;
    this.node = null;
    this.stream = null;
  }
}
