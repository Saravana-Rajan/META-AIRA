/**
 * WebSocket messages between the phone app and the backend.
 * Mirrors backend/aira/contracts.py exactly — keep the two in sync.
 */

export type FramePurpose = "ask" | "cash" | "count" | "invoice";
export type Cue = "ack" | "thinking" | "done" | "warn" | "match" | "mismatch" | "unsure";

// ---- phone -> server -------------------------------------------------------
export interface Hello {
  type: "hello";
  lang: string;
  key: string;
}

/** 80 ms of mono PCM16 little-endian at 24 kHz, base64. */
export interface Audio {
  type: "audio";
  pcm24: string;
}

export interface Frame {
  type: "frame";
  id: string;
  /** base64, no data: prefix */
  jpeg: string;
  w: number;
  h: number;
  purpose: FramePurpose;
  request_id: string | null;
}

/** Typed input (accessibility fallback and tests). */
export interface Text {
  type: "text";
  text: string;
}

export interface ConfirmAnswer {
  type: "confirm";
  id: string;
  answer: "yes" | "no";
}

export interface Stop {
  type: "stop";
}

export type Outbound = Hello | Audio | Frame | Text | ConfirmAnswer | Stop;

// ---- server -> phone -------------------------------------------------------
export interface Ready {
  type: "ready";
  session_id: string;
  mock: boolean;
}

export interface Transcript {
  type: "transcript";
  role: "user" | "aira";
  text: string;
  final: boolean;
}

/** Text for the phone to speak with on-device TTS. */
export interface Say {
  type: "say";
  text: string;
  lang: string;
}

export interface RequestFrame {
  type: "request_frame";
  purpose: FramePurpose;
  request_id: string;
}

export interface Box {
  left: number;
  top: number;
  right: number;
  bottom: number;
  score: number | null;
}

export interface Count {
  type: "count";
  sku: string;
  counted: number;
  expected: number | null;
  boxes: Box[];
  frame_id: string | null;
}

export type ConfirmKind = "sale" | "payment" | "delivery" | "stock" | "generic";

export interface Confirm {
  type: "confirm";
  id: string;
  kind: ConfirmKind;
  prompt: string;
}

export interface State {
  type: "state";
  entity: string;
  id: string;
  state: string;
  summary: string;
}

export interface CueMsg {
  type: "cue";
  cue: Cue;
}

export interface ErrorMsg {
  type: "error";
  code: string;
  say: string;
}

export type Inbound =
  | Ready
  | Transcript
  | Say
  | RequestFrame
  | Count
  | Confirm
  | State
  | CueMsg
  | ErrorMsg;

const INBOUND_TYPES: ReadonlySet<string> = new Set([
  "ready",
  "transcript",
  "say",
  "request_frame",
  "count",
  "confirm",
  "state",
  "cue",
  "error",
]);

/** Parse a server frame; returns null for anything unknown or malformed. Fills pydantic defaults. */
export function parseInbound(raw: string): Inbound | null {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof data !== "object" || data === null) return null;
  const d = data as Record<string, unknown>;
  if (typeof d.type !== "string" || !INBOUND_TYPES.has(d.type)) return null;
  switch (d.type) {
    case "transcript":
      return { final: true, ...d } as unknown as Transcript;
    case "say":
      return { lang: "en", ...d } as unknown as Say;
    case "count":
      return { expected: null, boxes: [], frame_id: null, ...d } as unknown as Count;
    case "state":
      return { summary: "", ...d } as unknown as State;
    case "error":
      return { say: "", ...d } as unknown as ErrorMsg;
    default:
      return d as unknown as Inbound;
  }
}
