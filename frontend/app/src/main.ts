import "./style.css";
import { unlockAudio } from "./audio";
import { Camera } from "./camera";
import { DeviceCheck } from "./check";
import { Connection, loadAppKey, resolveWsUrl, type ConnState } from "./connection";
import type { Confirm, Count, Inbound, State } from "./contracts";
import { cue } from "./cues";
import { attachGestures } from "./gestures";
import { LANGS, STRINGS, loadLang, saveLang, type Lang } from "./i18n";
import { Mic } from "./mic";
import { mountOrbs, setLevel } from "./orb";
import { Speaker } from "./speech";

// ---- DOM -------------------------------------------------------------------
function $<T extends HTMLElement = HTMLElement>(id: string): T {
  const el = document.getElementById(id);
  if (!el) throw new Error(`#${id} missing`);
  return el as T;
}
const root = document.documentElement;
const welcome = $("welcome");
const checkScreen = $("check");
const app = $("app");
const startBtn = $<HTMLButtonElement>("start");
const langSeg = $("lang-seg");
const optSr = $<HTMLInputElement>("opt-sr");
const optCam = $<HTMLInputElement>("opt-cam");

const pad = $<HTMLButtonElement>("pad");
const screen = $("screen");
const chipText = $("chip-text");
const mockTag = $("mock");
const camCard = $("cam-card");
const video = $<HTMLVideoElement>("cam");
const caps = $("caps");
const capHint = $("cap-hint");
const capUserWrap = $("cap-user-wrap");
const capAiraWrap = $("cap-aira-wrap");
const capUser = $("cap-user");
const capAira = $("cap-aira");
const task = $("task");
const taskIcon = $("task-icon");
const taskTitle = $("task-title");
const taskDetail = $("task-detail");
const taskBar = $("task-bar");
const taskFill = $("task-fill");
const taskHint = $("task-hint");
const footHint = $("foot-hint");

const langSelect = $<HTMLSelectElement>("lang");
const typeToggle = $<HTMLButtonElement>("type-toggle");
const camToggle = $<HTMLButtonElement>("cam-toggle");
const typeForm = $<HTMLFormElement>("type-form");
const typeInput = $<HTMLInputElement>("type-input");
const typeSend = $<HTMLButtonElement>("type-send");

const confirmBox = $("confirm");
const confirmTitle = $("confirm-title");
const confirmPrompt = $("confirm-prompt");
const confirmYes = $<HTMLButtonElement>("confirm-yes");
const confirmNo = $<HTMLButtonElement>("confirm-no");
const announceEl = $("announce");
const statusEl = $("status");

const [startOrb, voiceOrb] = mountOrbs();

// ---- settings ----------------------------------------------------------------
interface Settings {
  screenReader: boolean;
  showCamera: boolean;
}
const SETTINGS_KEY = "aira.settings";
function loadSettings(): Settings {
  try {
    const raw = JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? "{}") as Partial<Settings>;
    return { screenReader: raw.screenReader === true, showCamera: raw.showCamera === true };
  } catch {
    return { screenReader: false, showCamera: false };
  }
}
function saveSettings(): void {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch {
    /* storage blocked */
  }
}

// ---- state -----------------------------------------------------------------
const appKey = loadAppKey(); // strips ?key= from the URL before anything else
let lang: Lang = loadLang();
const settings = loadSettings();
let started = false;
let talking = false;
let thinking = false;
let looking = false;
let connState: ConnState = "connecting";
let lastAira = "";
let turnHadSpeech = false;
let pendingConfirm: Confirm | null = null;
let countChip: { text: string; until: number } | null = null;
let gestureUnlocked = false;
let cameraFailed = false;
const t = () => STRINGS[lang];

const CAPTION_MAX = 140;
const OK_STATE = /PLACED|RECONCILED|COMPLETED|CONFIRMED|CLOSED|PAID|DONE/i;
const BAD_STATE = /DISCREPANCY|FAILED|VOID|REJECTED|CANCEL/i;
const MARKS = {
  neutral: "M6.5 12h.01M12 12h.01M17.5 12h.01",
  ok: "M5 12.5l4.5 4.5L19 7.5",
  warn: "M12 6v7.5M12 18h.01",
  danger: "M7 7l10 10M17 7L7 17",
  confirm: "M9.2 9.2a2.8 2.8 0 1 1 3.9 2.6c-.7.3-1.1.9-1.1 1.6v.4M12 18h.01",
} as const;
type Tone = keyof typeof MARKS;

// ---- live regions ----------------------------------------------------------
function announce(text: string, assertive = true): void {
  const el = assertive ? announceEl : statusEl;
  // Clear first so repeating the same sentence is re-announced.
  el.textContent = "";
  window.setTimeout(() => {
    el.textContent = text;
  }, 60);
}

/** AIRA's own words go to the screen reader only in TalkBack mode (otherwise they would be heard twice). */
function announceAira(text: string): void {
  if (settings.screenReader) announce(text);
}

// ---- audio in/out ----------------------------------------------------------
const speaker = new Speaker(() => {
  updateMicGate();
  render();
});

const mic = new Mic(
  (pcm24) => {
    conn.send({ type: "audio", pcm24 });
  },
  (lv) => {
    if (voiceOrb) setLevel(voiceOrb, lv);
  },
);

const camera = new Camera(video);

/** Stream only while talking, connected, and AIRA is silent (echo guard). */
function updateMicGate(): void {
  mic.setStreaming(talking && !speaker.speaking && connState === "open");
}

/** Speak and resolve when finished (or after a watchdog), for the device check. */
function speakAndWait(text: string): Promise<void> {
  if (!speaker.supported) return new Promise((r) => window.setTimeout(r, 600));
  speaker.say(text, lang);
  const until = performance.now() + 4000 + text.length * 110;
  return new Promise((resolve) => {
    const tick = (): void => {
      if (!speaker.speaking || performance.now() > until) resolve();
      else window.setTimeout(tick, 120);
    };
    window.setTimeout(tick, 250);
  });
}

// ---- connection ------------------------------------------------------------
const conn = new Connection({
  url: resolveWsUrl(),
  hello: () => ({ lang, key: appKey }),
  onMessage: handle,
  onState: (s) => {
    const prev = connState;
    connState = s;
    root.dataset.conn = s;
    const label =
      s === "open" ? t().connected : s === "reconnecting" ? t().reconnecting : s === "offline" ? t().offline : null;
    if (s === "open") {
      cue("connected");
      if (label) announce(label, false);
    } else if (prev === "open") {
      cue("disconnected");
      thinking = false;
      looking = false;
      if (label) announce(label, false);
    }
    updateMicGate();
    render();
  },
});

function handle(msg: Inbound): void {
  switch (msg.type) {
    case "ready":
      mockTag.hidden = !msg.mock;
      if (msg.mock) announce(`${t().connected}. ${t().mockMode}`, false);
      break;
    case "transcript":
      if (msg.role === "user") {
        // A new user line starts a new turn: the previous AIRA reply leaves the card.
        setCaptions(msg.text, "", !msg.final);
        turnHadSpeech = true;
        if (msg.final && !talking) thinking = true;
      } else {
        lastAira = msg.text;
        setAira(msg.text);
        thinking = false;
        if (msg.final) announceAira(msg.text);
      }
      break;
    case "say":
      if (msg.text !== lastAira) {
        lastAira = msg.text;
        setAira(msg.text);
        announceAira(msg.text);
      }
      thinking = false;
      speaker.say(msg.text, msg.lang || lang);
      break;
    case "request_frame":
      void sendFrame(msg.purpose, msg.request_id);
      break;
    case "count":
      onCount(msg);
      break;
    case "confirm":
      showConfirm(msg);
      break;
    case "state":
      onState(msg);
      break;
    case "cue":
      if (msg.cue === "thinking") thinking = true;
      if (msg.cue === "done") thinking = false;
      cue(msg.cue);
      break;
    case "error":
      cue("warn");
      thinking = false;
      looking = false;
      if (msg.say) {
        lastAira = msg.say;
        setAira(msg.say);
        announceAira(msg.say);
        speaker.say(msg.say, lang);
      }
      break;
  }
  render();
}

async function sendFrame(purpose: Parameters<Camera["capture"]>[0], requestId: string): Promise<void> {
  looking = true;
  cue("working");
  render();
  try {
    const frame = await camera.capture(purpose, requestId);
    conn.send(frame);
    thinking = true;
  } catch {
    cue("warn");
    announce(t().cameraError);
    showTask("danger", t().camStep, t().cameraError);
  } finally {
    looking = false;
    render();
  }
}

function onCount(msg: Count): void {
  const text = t().counted(msg.counted, msg.sku, msg.expected);
  const exp = msg.expected;
  const tone: Tone = exp == null ? "neutral" : msg.counted === exp ? "ok" : "warn";
  showTask(tone, t().count, text, exp ? msg.counted / exp : undefined);
  countChip = { text: exp == null ? `${t().chip.counting} ${msg.counted}` : `${t().chip.counting} ${msg.counted} / ${exp}`, until: Date.now() + 4000 };
  window.setTimeout(render, 4100);
  if (exp != null) cue(msg.counted === exp ? "match" : "mismatch");
  announce(text, false);
}

function onState(msg: State): void {
  const tone: Tone = BAD_STATE.test(msg.state) ? "danger" : OK_STATE.test(msg.state) ? "ok" : "neutral";
  showTask(tone, t().entity[msg.entity] ?? msg.entity, msg.summary || msg.state);
}

// ---- captions + task card ------------------------------------------------------
/** Keep the newest words: cut long lines from the front at a word boundary. */
function tail(text: string): string {
  const s = text.trim();
  if (s.length <= CAPTION_MAX) return s;
  const cut = s.slice(s.length - CAPTION_MAX);
  const sp = cut.indexOf(" ");
  return `… ${sp > 0 && sp < 24 ? cut.slice(sp + 1) : cut}`;
}

function setCaptions(user: string, aira: string, partial = false): void {
  capUser.textContent = tail(user);
  capUser.dataset.partial = String(partial);
  capUserWrap.hidden = !user;
  setAira(aira);
}

function setAira(text: string): void {
  capAira.textContent = tail(text);
  capAiraWrap.hidden = !text;
  const empty = capUserWrap.hidden && capAiraWrap.hidden;
  caps.classList.toggle("caps--empty", empty);
  capHint.hidden = !empty;
  footHint.hidden = empty;
}

function showTask(tone: Tone, title: string, detail: string, progress?: number, hint?: string): void {
  // Restart the entrance animation for a new card.
  task.hidden = true;
  void task.offsetWidth;
  task.dataset.tone = tone;
  taskIcon.setAttribute("d", MARKS[tone]);
  taskTitle.textContent = title;
  taskDetail.textContent = detail;
  taskBar.hidden = progress === undefined;
  taskFill.style.setProperty("--fill", String(Math.min(1, Math.max(0, progress ?? 0))));
  taskHint.hidden = !hint;
  taskHint.textContent = hint ?? "";
  task.hidden = false;
}

function hideTask(): void {
  task.hidden = true;
}

// ---- confirm -------------------------------------------------------------------
function showConfirm(msg: Confirm): void {
  pendingConfirm = msg;
  thinking = false;
  const title = t().confirmKind[msg.kind] ?? t().confirmTitle;
  confirmTitle.textContent = title;
  confirmPrompt.textContent = msg.prompt;
  showTask("confirm", title, msg.prompt, undefined, t().confirmHint);
  confirmBox.hidden = false;
  cue("confirm");
  announce(`${msg.prompt} ${t().confirmHint}`);
  confirmYes.focus();
  render();
}

function closeConfirm(): void {
  pendingConfirm = null;
  confirmBox.hidden = true;
  if (task.dataset.tone === "confirm") hideTask();
  pad.focus();
  render();
}

function answerConfirm(answer: "yes" | "no"): void {
  if (!pendingConfirm) return closeConfirm();
  conn.send({ type: "confirm", id: pendingConfirm.id, answer });
  cue(answer === "yes" ? "ack" : "talk_off");
  thinking = true;
  closeConfirm();
}
confirmYes.addEventListener("click", () => answerConfirm("yes"));
confirmNo.addEventListener("click", () => answerConfirm("no"));
confirmBox.addEventListener("keydown", (e) => {
  if (e.key === "Escape") answerConfirm("no");
});

// ---- talking / stop -----------------------------------------------------------
async function toggleTalk(): Promise<void> {
  if (!talking) {
    speaker.cancel(); // barge-in
    talking = true;
    thinking = false;
    turnHadSpeech = false;
    cue("talk_on");
    render();
    announce(t().talkOn, false);
    try {
      await mic.start();
    } catch {
      talking = false;
      cue("warn");
      announce(t().micError);
      showTask("danger", t().micStep, t().micError);
    }
  } else {
    talking = false;
    if (turnHadSpeech) thinking = true;
    cue("talk_off");
    announce(t().talkOff, false);
  }
  updateMicGate();
  render();
}

function stopAll(): void {
  speaker.cancel();
  talking = false;
  thinking = false;
  looking = false;
  updateMicGate();
  conn.send({ type: "stop" });
  if (pendingConfirm) {
    pendingConfirm = null;
    confirmBox.hidden = true;
    if (task.dataset.tone === "confirm") hideTask();
  }
  cue("stop");
  announce(t().stopped);
  render();
}

function unlockMedia(): void {
  unlockAudio();
  if (gestureUnlocked) return;
  gestureUnlocked = true;
  speaker.unlock();
}

function startCamera(): void {
  camera
    .start()
    .then(() => {
      video.dataset.live = "true";
      render();
    })
    .catch(() => {
      if (!cameraFailed) {
        cameraFailed = true;
        showTask("warn", t().camStep, t().cameraError);
      }
    });
}

attachGestures(pad, {
  onAnyGesture: unlockMedia,
  onDoubleTap: () => {
    if (pendingConfirm) answerConfirm("yes");
    else void toggleTalk();
  },
  onLongPress: () => {
    if (pendingConfirm) answerConfirm("no");
    else stopAll();
  },
  onSingleTap: () => {
    // Single tap: repeat the last AIRA line (or the hint).
    if (lastAira) speaker.say(lastAira, lang);
    else announce(t().hint);
  },
});
// Any first touch elsewhere (language picker, type box) also unlocks audio on iOS.
document.addEventListener("pointerdown", unlockAudio, { capture: true });

// ---- typed input ---------------------------------------------------------------
typeToggle.addEventListener("click", () => {
  const open = typeForm.hidden;
  typeForm.hidden = !open;
  typeToggle.setAttribute("aria-expanded", String(open));
  if (open) typeInput.focus();
});
typeForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = typeInput.value.trim();
  if (!text) return;
  speaker.cancel();
  if (conn.send({ type: "text", text })) {
    setCaptions(text, "");
    typeInput.value = "";
    thinking = true;
    cue("ack");
    render();
  } else {
    cue("warn");
    announce(t().reconnecting);
  }
});

// ---- camera card -----------------------------------------------------------------
function setShowCamera(on: boolean): void {
  settings.showCamera = on;
  optCam.checked = on;
  saveSettings();
  render();
}
camToggle.addEventListener("click", () => setShowCamera(!settings.showCamera));
optCam.addEventListener("change", () => setShowCamera(optCam.checked));
optSr.addEventListener("change", () => {
  settings.screenReader = optSr.checked;
  saveSettings();
});

// ---- language ------------------------------------------------------------------
function setLang(v: Lang): void {
  if (v === lang) return;
  lang = v;
  saveLang(lang);
  renderText();
  speaker.cancel();
  announce(STRINGS[lang].langName, false);
  if (started) conn.restart(); // new hello with the new language
}

function buildLangControls(): void {
  for (const l of LANGS) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "seg-btn";
    b.setAttribute("role", "radio");
    b.dataset.lang = l;
    b.lang = l;
    b.textContent = STRINGS[l].langName;
    b.addEventListener("click", () => setLang(l));
    langSeg.appendChild(b);

    const o = document.createElement("option");
    o.value = l;
    o.lang = l;
    o.textContent = STRINGS[l].langName;
    langSelect.appendChild(o);
  }
  langSeg.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const i = LANGS.indexOf(lang) + (e.key === "ArrowRight" ? 1 : -1);
    const next = LANGS[(i + LANGS.length) % LANGS.length];
    if (next) {
      setLang(next);
      langSeg.querySelector<HTMLElement>(`[data-lang="${next}"]`)?.focus();
    }
  });
  langSelect.addEventListener("change", () => {
    const v = langSelect.value as Lang;
    if (LANGS.includes(v)) setLang(v);
  });
}

function renderText(): void {
  const s = t();
  root.lang = lang;
  $("start-label").textContent = s.tapToStart;
  $("start-hint").textContent = s.startHint;
  $("lang-label").textContent = s.language;
  $("lang-hint").textContent = s.languageHint;
  $("sr-label").textContent = s.talkback;
  $("sr-hint").textContent = s.talkbackHint;
  $("cam-label").textContent = s.showCamera;
  $("cam-hint").textContent = s.showCameraHint;
  for (const b of langSeg.querySelectorAll<HTMLElement>(".seg-btn")) {
    const on = b.dataset.lang === lang;
    b.setAttribute("aria-checked", String(on));
    b.tabIndex = on ? 0 : -1;
  }
  langSelect.value = lang;
  $("tool-lang-label").textContent = s.language;
  capHint.textContent = s.hint;
  footHint.textContent = s.hint;
  $("who-you").textContent = s.you;
  $("who-aira").textContent = s.aira;
  $("cam-badge-text").textContent = s.camStep;
  mockTag.textContent = s.mockMode;
  typeToggle.textContent = s.type;
  typeInput.placeholder = s.typePlaceholder;
  $("type-label").textContent = s.typeLabel;
  typeSend.textContent = s.send;
  confirmYes.textContent = s.yes;
  confirmNo.textContent = s.no;
  if (!pendingConfirm) confirmTitle.textContent = s.confirmTitle;
  check.render();
  render();
}

// ---- status chip + surface -----------------------------------------------------------
function status(): { kind: string; label: string } {
  const c = t().chip;
  if (connState === "connecting") return { kind: "connecting", label: c.connecting };
  if (connState === "reconnecting") return { kind: "warning", label: c.reconnecting };
  if (connState === "offline") return { kind: "warning", label: c.offline };
  if (talking) return { kind: "listening", label: c.listening };
  if (speaker.speaking) return { kind: "speaking", label: c.speaking };
  if (looking) return { kind: "thinking", label: c.looking };
  if (thinking) return { kind: "thinking", label: c.thinking };
  if (countChip && countChip.until > Date.now()) return { kind: "ready", label: countChip.text };
  return { kind: "ready", label: c.ready };
}

function render(): void {
  const st = status();
  screen.dataset.status = st.kind;
  chipText.textContent = st.label;
  root.dataset.talking = String(talking);
  pad.setAttribute("aria-label", t().buttonLabel(talking, pendingConfirm !== null));
  pad.setAttribute("aria-pressed", String(talking));
  camCard.hidden = !(settings.showCamera && video.dataset.live === "true");
  camToggle.setAttribute("aria-pressed", String(settings.showCamera));
  camToggle.textContent = settings.showCamera ? t().hideCamera : t().showCamera;
}

// ---- flow: Start -> Device check -> Voice screen ------------------------------------
const check = new DeviceCheck({
  camera,
  video,
  t,
  lang: () => lang,
  speak: speakAndWait,
  onDone: openVoiceScreen,
});

startBtn.addEventListener("click", () => {
  if (started) return; // a double tap must not run setup twice
  started = true;
  unlockMedia();
  if (startOrb) setLevel(startOrb, 0);
  conn.connect();
  welcome.hidden = true;
  checkScreen.hidden = false;
  window.scrollTo(0, 0);
  void check.run();
});

function openVoiceScreen(): void {
  speaker.cancel();
  unlockAudio();
  checkScreen.hidden = true;
  app.hidden = false;
  window.scrollTo(0, 0);
  startCamera();
  render();
  pad.focus({ preventScroll: true, focusVisible: false } as FocusOptions);
  announce(t().hint, false);
}

// ---- boot --------------------------------------------------------------------
buildLangControls();
optSr.checked = settings.screenReader;
optCam.checked = settings.showCamera;
renderText();
setCaptions("", "");
root.dataset.conn = "connecting";
startBtn.focus({ preventScroll: true, focusVisible: false } as FocusOptions);

if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("./sw.js").catch(() => undefined);
  });
}
