/*
 * AudioWorklet: mono float input at the context rate -> 24 kHz PCM16 LE,
 * posted as 80 ms chunks (1920 samples = 3840 bytes) as transferable ArrayBuffers.
 * Plain JS (served as-is from /public) so it loads in every browser without bundling.
 */
const OUT_RATE = 24000;
const CHUNK = 1920;

class Pcm24Processor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / OUT_RATE; // input samples per output sample
    this.pos = 0; // fractional read position into `this.prev`+current block
    this.last = 0; // last input sample of the previous block (for interpolation)
    this.out = new Int16Array(CHUNK);
    this.n = 0;
    this.enabled = true;
    this.port.onmessage = (e) => {
      if (e.data && typeof e.data.enabled === "boolean") {
        this.enabled = e.data.enabled;
        if (!this.enabled) {
          this.n = 0;
          this.pos = 0;
        }
      }
    };
  }

  push(sample) {
    const s = Math.max(-1, Math.min(1, sample));
    this.out[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
    if (this.n === CHUNK) {
      const buf = this.out.buffer;
      this.port.postMessage(buf, [buf]);
      this.out = new Int16Array(CHUNK);
      this.n = 0;
    }
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0 || !this.enabled) return true;
    // Downmix to mono.
    const len = input[0].length;
    const mono = new Float32Array(len);
    for (let c = 0; c < input.length; c++) {
      const ch = input[c];
      for (let i = 0; i < len; i++) mono[i] += ch[i];
    }
    if (input.length > 1) for (let i = 0; i < len; i++) mono[i] /= input.length;

    if (this.ratio === 1) {
      for (let i = 0; i < len; i++) this.push(mono[i]);
      return true;
    }
    // Linear interpolation. Index -1 refers to `this.last`.
    let pos = this.pos;
    while (pos < len - 1) {
      const i = Math.floor(pos);
      const frac = pos - i;
      const a = i < 0 ? this.last : mono[i];
      const b = mono[i + 1];
      this.push(a + (b - a) * frac);
      pos += this.ratio;
    }
    this.pos = pos - len;
    this.last = mono[len - 1];
    return true;
  }
}

registerProcessor("pcm24", Pcm24Processor);
