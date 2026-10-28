/** On-device TTS via speechSynthesis, with per-language voice choice and barge-in cancel. */

const LANG_TAGS: Record<string, string[]> = {
  en: ["en-IN", "en-GB", "en-US", "en"],
  ta: ["ta-IN", "ta"],
  hi: ["hi-IN", "hi"],
};

export function bcp47(lang: string): string {
  const short = lang.slice(0, 2).toLowerCase();
  return LANG_TAGS[short]?.[0] ?? lang;
}

export class Speaker {
  private voices: SpeechSynthesisVoice[] = [];
  private queue: SpeechSynthesisUtterance[] = [];
  private speakingNow = false;
  private generation = 0;
  private watchdog: number | undefined;

  constructor(private readonly onSpeaking: (speaking: boolean) => void) {
    const synth = this.synth;
    if (!synth) return;
    const load = (): void => {
      this.voices = synth.getVoices();
    };
    load();
    synth.addEventListener?.("voiceschanged", load);
  }

  private get synth(): SpeechSynthesis | null {
    return "speechSynthesis" in window ? window.speechSynthesis : null;
  }

  get supported(): boolean {
    return this.synth !== null;
  }

  get speaking(): boolean {
    return this.speakingNow;
  }

  /** iOS: speechSynthesis must be primed from a user gesture. */
  unlock(): void {
    const synth = this.synth;
    if (!synth || this.speakingNow) return;
    const u = new SpeechSynthesisUtterance(" ");
    u.volume = 0;
    synth.speak(u);
  }

  pickVoice(lang: string): SpeechSynthesisVoice | null {
    if (this.voices.length === 0 && this.synth) this.voices = this.synth.getVoices();
    const short = lang.slice(0, 2).toLowerCase();
    const wanted = LANG_TAGS[short] ?? [lang];
    const norm = (s: string): string => s.replace("_", "-").toLowerCase();
    for (const tag of wanted) {
      const t = norm(tag);
      const exact = this.voices.filter((v) => norm(v.lang) === t);
      const hit = exact.find((v) => v.localService) ?? exact[0];
      if (hit) return hit;
    }
    const prefix = this.voices.find((v) => norm(v.lang).startsWith(short));
    if (prefix) return prefix;
    // Fallback: Indian English, then any English.
    return (
      this.voices.find((v) => norm(v.lang) === "en-in") ??
      this.voices.find((v) => norm(v.lang).startsWith("en")) ??
      null
    );
  }

  /** Speak text; queued after anything already speaking. */
  say(text: string, lang: string): void {
    const synth = this.synth;
    const clean = text.trim();
    if (!synth || !clean) return;
    const voice = this.pickVoice(lang);
    const gen = this.generation;
    // Chrome cuts long utterances (~15 s); split into sentences.
    const parts = clean.match(/[^.!?।\n]+[.!?।]*\s*/gu) ?? [clean];
    for (const part of parts) {
      const p = part.trim();
      if (!p) continue;
      const u = new SpeechSynthesisUtterance(p);
      u.lang = voice?.lang ?? bcp47(lang);
      if (voice) u.voice = voice;
      u.rate = 1.0;
      u.onstart = () => {
        if (gen === this.generation) this.setSpeaking(true);
      };
      const done = (): void => {
        this.queue = this.queue.filter((q) => q !== u);
        if (gen === this.generation && this.queue.length === 0) this.setSpeaking(false);
      };
      u.onend = done;
      u.onerror = done;
      this.queue.push(u);
      synth.speak(u);
    }
    // Mark speaking immediately so the mic is muted before audio actually starts.
    this.setSpeaking(true);
    this.armWatchdog(clean.length);
  }

  /** Cancel everything (barge-in, stop). */
  cancel(): void {
    this.generation += 1;
    this.queue = [];
    this.synth?.cancel();
    this.setSpeaking(false);
  }

  private armWatchdog(chars: number): void {
    // If the engine never fires onend (it happens on some Android builds), unmute anyway.
    window.clearTimeout(this.watchdog);
    const ms = 3000 + chars * 120;
    const gen = this.generation;
    this.watchdog = window.setTimeout(() => {
      if (gen === this.generation && this.speakingNow && !this.synth?.speaking) {
        this.queue = [];
        this.setSpeaking(false);
      }
    }, ms);
  }

  private setSpeaking(on: boolean): void {
    if (on === this.speakingNow) return;
    this.speakingNow = on;
    if (!on) window.clearTimeout(this.watchdog);
    this.onSpeaking(on);
  }
}
