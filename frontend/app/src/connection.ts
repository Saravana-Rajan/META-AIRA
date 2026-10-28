import { parseInbound, type Inbound, type Outbound } from "./contracts";

export type ConnState = "connecting" | "open" | "reconnecting" | "offline";

export interface ConnectionOptions {
  url: string;
  hello: () => { lang: string; key: string };
  onMessage: (msg: Inbound) => void;
  onState: (state: ConnState, attempt: number) => void;
}

/**
 * Resolve the backend WebSocket URL.
 * Security: the `?ws=` override is honoured ONLY in dev builds — in production a crafted link
 * must not be able to redirect the app key, mic audio and camera frames to another server.
 *  - dev:  ?ws= -> VITE_BACKEND_WS -> ws://localhost:8080/ws
 *  - prod: VITE_BACKEND_WS (build time) -> same-origin wss://<host>/ws
 */
export function resolveWsUrl(): string {
  const env = (import.meta.env.VITE_BACKEND_WS as string | undefined)?.trim();
  if (import.meta.env.DEV) {
    const q = new URLSearchParams(location.search).get("ws");
    if (q) return q;
    return env || "ws://localhost:8080/ws";
  }
  if (env) return env;
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/ws`;
}

const KEY_STORAGE = "aira.key";

/**
 * The shared demo key (as in the original AIRA, the plain URL just works). It lives only in Cloud
 * Run and is fetched from the configured backend's /api/client-config at startup, so it is never
 * in GitHub or the bundle; the backend caps model spend per caller.
 * A `?key=` link overrides it (kept in localStorage, stripped from the address bar).
 */
export async function fetchServerKey(wsUrl: string): Promise<string> {
  const base = wsUrl.replace(/^ws/, "http").replace(/\/ws\/?$/, "");
  try {
    const r = await fetch(`${base}/api/client-config`, { cache: "no-store" });
    if (!r.ok) return "";
    const body = (await r.json()) as { app_key?: unknown };
    return typeof body.app_key === "string" ? body.app_key : "";
  } catch {
    return ""; // offline: hello goes without a key and the connection retries
  }
}

export function clearStoredKey(): void {
  try {
    localStorage.removeItem(KEY_STORAGE);
  } catch {
    /* storage blocked: nothing stored */
  }
}

/** A `?key=` override or a previously stored one. Call once at startup, before anything else reads the URL. */
export function loadAppKey(): string {
  const url = new URL(location.href);
  const fromUrl = url.searchParams.get("key");
  if (fromUrl !== null) {
    try {
      localStorage.setItem(KEY_STORAGE, fromUrl);
    } catch {
      /* storage blocked: key lives only for this page load */
    }
    url.searchParams.delete("key");
    history.replaceState(history.state, "", url.pathname + url.search + url.hash);
    return fromUrl;
  }
  try {
    return localStorage.getItem(KEY_STORAGE) ?? "";
  } catch {
    return "";
  }
}

const MIN_BACKOFF_MS = 500;
const MAX_BACKOFF_MS = 10_000;

/** A WebSocket that sends hello on every open and reconnects with jittered exponential backoff. */
export class Connection {
  private ws: WebSocket | null = null;
  private attempt = 0;
  private timer: number | undefined;
  private closedByUser = false;
  private state: ConnState = "connecting";

  constructor(private readonly opts: ConnectionOptions) {}

  get isOpen(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  connect(): void {
    this.closedByUser = false;
    window.clearTimeout(this.timer);
    this.setState(this.attempt === 0 ? "connecting" : "reconnecting");
    let ws: WebSocket;
    try {
      ws = new WebSocket(this.opts.url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      if (this.ws !== ws) return;
      this.attempt = 0;
      const { lang, key } = this.opts.hello();
      ws.send(JSON.stringify({ type: "hello", lang, key } satisfies Outbound));
      this.setState("open");
    };
    ws.onmessage = (ev: MessageEvent) => {
      if (this.ws !== ws || typeof ev.data !== "string") return;
      const msg = parseInbound(ev.data);
      if (msg) this.opts.onMessage(msg);
    };
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      if (!this.closedByUser) this.scheduleReconnect();
    };
    ws.onerror = () => {
      /* onclose follows */
    };
  }

  /** Drop the socket and reconnect now (e.g. after a language change). */
  restart(): void {
    const old = this.ws;
    this.ws = null;
    old?.close();
    this.attempt = 0;
    this.connect();
  }

  send(msg: Outbound): boolean {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return false;
    // Do not let audio pile up on a slow link: drop chunks if >256 KB is buffered.
    if (msg.type === "audio" && this.ws.bufferedAmount > 256 * 1024) return false;
    this.ws.send(JSON.stringify(msg));
    return true;
  }

  private scheduleReconnect(): void {
    this.attempt += 1;
    this.setState(navigator.onLine === false ? "offline" : "reconnecting");
    const base = Math.min(MAX_BACKOFF_MS, MIN_BACKOFF_MS * 2 ** (this.attempt - 1));
    const delay = base / 2 + Math.random() * (base / 2);
    window.clearTimeout(this.timer);
    this.timer = window.setTimeout(() => this.connect(), delay);
  }

  private setState(s: ConnState): void {
    if (s === this.state && s !== "reconnecting") return;
    this.state = s;
    this.opts.onState(s, this.attempt);
  }
}
