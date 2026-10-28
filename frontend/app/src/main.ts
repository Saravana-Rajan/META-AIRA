import "./style.css";
import { unlockAudio } from "./audio";
import { Camera } from "./camera";
import { Connection, loadAppKey, resolveWsUrl, type ConnState } from "./connection";
import type { Confirm, Count, Inbound } from "./contracts";
import { cue } from "./cues";
import { attachGestures } from "./gestures";
import { LANGS, STRINGS, loadLang, saveLang, type Lang } from "./i18n";
import { Mic } from "./mic";
import { Speaker } from "./speech";

// ---- DOM -------------------------------------------------------------------
function $<T extends HTMLElement>(id: string): T {
  const el = document.getElementById(id);
  if (!el) throw new Error(`#${id} missing`);
  return el as T;
}
const root = document.documentElement;
const pad = $<HTMLButtonElement>("pad");
const padState = $("pad-state");
const padHint = $("pad-hint");
const connText = $("conn-text");
const mockBadge = $("mock");
const langSelect = $<HTMLSelectElement>("lang");
const capUser = $("cap-user");
const capAira = $("cap-aira");
const capMeta = $("cap-meta");
const video = $<HTMLVideoElement>("cam");
const typeSummary = $("type-summary");
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

// ---- state -----------------------------------------------------------------
const appKey = loadAppKey(); // strips ?key= from the URL before anything else
let lang: Lang = loadLang();
let talking = false;
let connState: ConnState = "connecting";
let lastAira = "";
let pendingConfirmId: string | null = null;
let gestureUnlocked = false;
let cameraFailed = false;
const t = () => STRINGS[lang];

// ---- live regions ----------------------------------------------------------
function announce(text: string, assertive = true): void {
  const el = assertive ? announceEl : statusEl;
  // Clear first so repeating the same sentence is re-announced.
  el.textContent = "";
  window.setTimeout(() => {
    el.textContent = text;
  }, 60);
}

// ---- audio in/out ----------------------------------------------------------
const speaker = new Speaker((speaking) => {
  root.dataset.speaking = String(speaking);
  updateMicGate();
  renderPad();
});

const mic = new Mic((pcm24) => {
  conn.send({ type: "audio", pcm24 });
});

const camera = new Camera(video);

/** Stream only while talking, connected, and AIRA is silent (echo guard). */
function updateMicGate(): void {
  mic.setStreaming(talking && !speaker.speaking && connState === "open");
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
      s === "open" ? t().connected : s === "reconnecting" ? t().reconnecting : s === "offline" ? t().offline : t().connecting;
    connText.textContent = label;
    if (s === "open") {
      cue("connected");
      announce(label, false);
    } else if (prev === "open") {
      cue("disconnected");
      announce(label, false);
    }
    updateMicGate();
  },
});

function handle(msg: Inbound): void {
  switch (msg.type) {
    case "ready":
      mockBadge.hidden = !msg.mock;
      if (msg.mock) announce(`${t().connected}. ${t().mockMode}`, false);
      break;
    case "transcript":
      if (msg.role === "user") {
        capUser.textContent = msg.text;
        capUser.dataset.partial = String(!msg.final);
      } else {
        lastAira = msg.text;
        capAira.textContent = msg.text;
        if (msg.final) announce(msg.text);
      }
      break;
    case "say":
      if (msg.text !== lastAira) {
        lastAira = msg.text;
        capAira.textContent = msg.text;
      }
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
      capMeta.textContent = msg.summary || `${msg.entity}: ${msg.state}`;
      break;
    case "cue":
      cue(msg.cue);
      break;
    case "error":
      cue("warn");
      if (msg.say) {
        capAira.textContent = msg.say;
        lastAira = msg.say;
        announce(msg.say);
        speaker.say(msg.say, lang);
      }
      break;
  }
}

async function sendFrame(purpose: Parameters<Camera["capture"]>[0], requestId: string): Promise<void> {
  capMeta.textContent = t().lookingCamera;
  try {
    const frame = await camera.capture(purpose, requestId);
    video.dataset.live = "true";
    conn.send(frame);
  } catch {
    cue("warn");
    announce(t().cameraError);
    capMeta.textContent = t().cameraError;
  }
}

function onCount(msg: Count): void {
  const text = t().counted(msg.counted, msg.sku, msg.expected);
  capMeta.textContent = text;
  if (msg.expected != null) cue(msg.counted === msg.expected ? "match" : "mismatch");
  announce(text, false);
}

// ---- confirm overlay ---------------------------------------------------------
function showConfirm(msg: Confirm): void {
  pendingConfirmId = msg.id;
  confirmPrompt.textContent = msg.prompt;
  confirmBox.hidden = false;
  cue("confirm");
  announce(msg.prompt);
  confirmYes.focus();
}

function hideConfirm(): void {
  pendingConfirmId = null;
  confirmBox.hidden = true;
  pad.focus();
}

function answerConfirm(answer: "yes" | "no"): void {
  if (!pendingConfirmId) return hideConfirm();
  conn.send({ type: "confirm", id: pendingConfirmId, answer });
  cue(answer === "yes" ? "ack" : "talk_off");
  hideConfirm();
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
    cue("talk_on");
    renderPad();
    announce(t().talkOn, false);
    try {
      await mic.start();
    } catch {
      talking = false;
      cue("warn");
      announce(t().micError);
      capMeta.textContent = t().micError;
    }
  } else {
    talking = false;
    cue("talk_off");
    announce(t().talkOff, false);
  }
  updateMicGate();
  renderPad();
}

function stopAll(): void {
  speaker.cancel();
  talking = false;
  updateMicGate();
  conn.send({ type: "stop" });
  if (!confirmBox.hidden) {
    pendingConfirmId = null;
    confirmBox.hidden = true;
  }
  cue("stop");
  announce(t().stopped);
  renderPad();
}

function onFirstGesture(): void {
  unlockAudio();
  if (gestureUnlocked) return;
  gestureUnlocked = true;
  speaker.unlock();
  camera
    .start()
    .then(() => {
      video.dataset.live = "true";
    })
    .catch(() => {
      if (!cameraFailed) {
        cameraFailed = true;
        capMeta.textContent = t().cameraError;
      }
    });
}

attachGestures(pad, {
  onAnyGesture: onFirstGesture,
  onDoubleTap: () => void toggleTalk(),
  onLongPress: stopAll,
  onSingleTap: () => {
    // Single tap: repeat the last AIRA line (or the hint).
    if (lastAira) speaker.say(lastAira, lang);
    else announce(t().hint);
  },
});
// Any first touch elsewhere (language picker, type box) also unlocks audio on iOS.
document.addEventListener("pointerdown", onFirstGesture, { capture: true, once: false });

// ---- typed input -------------------------------------------------------------
typeForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = typeInput.value.trim();
  if (!text) return;
  speaker.cancel();
  if (conn.send({ type: "text", text })) {
    capUser.textContent = text;
    capUser.dataset.partial = "false";
    typeInput.value = "";
    cue("ack");
  } else {
    cue("warn");
    announce(t().reconnecting);
  }
});

// ---- language ----------------------------------------------------------------
function renderLang(): void {
  root.lang = lang;
  langSelect.value = lang;
  typeSummary.textContent = t().typeInstead;
  typeSend.textContent = t().send;
  confirmTitle.textContent = t().confirmTitle;
  confirmYes.textContent = t().yes;
  confirmNo.textContent = t().no;
  padHint.textContent = t().hint;
  mockBadge.textContent = t().mockMode;
  renderPad();
}

function renderPad(): void {
  root.dataset.talking = String(talking);
  padState.textContent = speaker.speaking ? "AIRA" : talking ? t().talkOn : "AIRA";
  pad.setAttribute("aria-label", t().buttonLabel(talking));
  pad.setAttribute("aria-pressed", String(talking));
}

langSelect.addEventListener("change", () => {
  const v = langSelect.value as Lang;
  if (!LANGS.includes(v) || v === lang) return;
  lang = v;
  saveLang(lang);
  renderLang();
  speaker.cancel();
  announce(STRINGS[lang].langName, false);
  conn.restart(); // new hello with the new language
});

// ---- boot --------------------------------------------------------------------
renderLang();
root.dataset.conn = "connecting";
conn.connect();
pad.focus();

if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("./sw.js").catch(() => undefined);
  });
}
