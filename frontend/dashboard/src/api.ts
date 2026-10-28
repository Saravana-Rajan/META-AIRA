import type { AiraEvent, ConnStatus, Snapshot } from './types';
import { arr, normEvent, normSnapshot, obj } from './util';

const DEFAULT_BACKEND = 'http://localhost:8080';

function trimBase(u: string): string {
  return u.trim().replace(/\/+$/, '');
}

/** The production backend: VITE_BACKEND_URL or the localhost default. The only origin that ever gets the app key. */
function trustedBase(): string {
  const env = import.meta.env.VITE_BACKEND_URL;
  return (env && trimBase(env)) || DEFAULT_BACKEND;
}

/**
 * ?api=... is honoured ONLY in dev builds (security: a production link must not be able to
 * redirect the stored app key to an attacker-supplied backend).
 */
export function resolveBase(): string {
  if (import.meta.env.DEV) {
    const q = new URLSearchParams(location.search).get('api');
    if (q && q.trim()) return trimBase(q);
  }
  return trustedBase();
}

const KEY_STORE = 'aira.dashboard.key';
let appKey = '';

/**
 * Read the app key from ?key= once, persist it in localStorage and strip it from the
 * address bar. Never baked into the bundle. Empty when the backend has no key (local mock).
 */
export function initKey(): void {
  const url = new URL(location.href);
  const q = url.searchParams.get('key');
  if (q !== null) {
    appKey = q.trim();
    try {
      if (appKey) localStorage.setItem(KEY_STORE, appKey);
      else localStorage.removeItem(KEY_STORE);
    } catch {
      /* storage blocked: keep key in memory only */
    }
    url.searchParams.delete('key');
    history.replaceState(history.state, '', url.pathname + url.search + url.hash);
    return;
  }
  try {
    appKey = localStorage.getItem(KEY_STORE) ?? '';
  } catch {
    appKey = '';
  }
}

/** Header for JSON fetches (/api/snapshot, /api/events). */
function authHeaders(base: string): Record<string, string> {
  return appKey && base === trustedBase() ? { 'X-Aira-Key': appKey } : {};
}

/** ?key= only for URLs that can't carry headers (EventSource, <img>). */
function withKey(base: string, url: string): string {
  if (!appKey || base !== trustedBase()) return url;
  return `${url}${url.includes('?') ? '&' : '?'}key=${encodeURIComponent(appKey)}`;
}

export async function fetchSnapshot(base: string, signal?: AbortSignal): Promise<Snapshot> {
  const r = await fetch(`${base}/api/snapshot`, { cache: 'no-store', signal, headers: authHeaders(base) });
  if (!r.ok) throw new Error(`snapshot ${r.status}`);
  return normSnapshot(await r.json());
}

export function frameUrl(base: string, frameId: string): string {
  return withKey(base, `${base}/api/frame/${encodeURIComponent(frameId)}`);
}

/** Kinds we subscribe to explicitly in case the server uses named SSE events. */
const NAMED_KINDS = [
  'sale',
  'sales',
  'payment',
  'payments',
  'ledger',
  'delivery',
  'deliveries',
  'count',
  'confirm',
  'transcript',
  'verification',
  'stock',
  'inventory',
  'state',
  'event',
];

export interface ConnHandlers {
  onEvent(e: AiraEvent): void;
  onStatus(s: ConnStatus): void;
  /** Called whenever the stream (re)connects, so the caller can re-fetch the snapshot. */
  onReconnect(): void;
}

/**
 * SSE with auto-reconnect (exponential backoff) and a JSON polling fallback that runs
 * while the stream is down. Events are de-duplicated by `seq`.
 */
export class LiveConnection {
  private es: EventSource | null = null;
  private pollTimer: number | null = null;
  private retryTimer: number | null = null;
  private retryMs = 1000;
  private lastSeq = 0;
  private seen = new Set<number>();
  private pollFailing = false;
  private polling = false;
  private stopped = false;

  constructor(
    private readonly base: string,
    private readonly h: ConnHandlers,
  ) {}

  start(): void {
    this.h.onStatus('connecting');
    this.openSse();
  }

  stop(): void {
    this.stopped = true;
    this.es?.close();
    this.es = null;
    this.stopPolling();
    if (this.retryTimer !== null) clearTimeout(this.retryTimer);
  }

  private openSse(): void {
    if (this.stopped) return;
    if (typeof EventSource === 'undefined') {
      this.startPolling();
      return;
    }
    let es: EventSource;
    try {
      const after = this.lastSeq ? `?after=${this.lastSeq}` : '';
      es = new EventSource(withKey(this.base, `${this.base}/api/stream${after}`));
    } catch {
      this.onSseDown();
      return;
    }
    this.es = es;
    es.onopen = () => {
      this.retryMs = 1000;
      this.stopPolling();
      this.h.onStatus('live');
      this.h.onReconnect();
    };
    const handle = (m: MessageEvent) => this.ingestRaw(m.data, m.type);
    es.onmessage = handle;
    for (const k of NAMED_KINDS) es.addEventListener(k, handle as EventListener);
    es.onerror = () => {
      // EventSource retries on its own, but some servers close cleanly and some proxies
      // hang; we take control: close, poll, and retry with backoff.
      es.close();
      if (this.es === es) this.es = null;
      this.onSseDown();
    };
  }

  private onSseDown(): void {
    if (this.stopped) return;
    this.startPolling();
    if (this.retryTimer !== null) clearTimeout(this.retryTimer);
    this.retryTimer = window.setTimeout(() => {
      this.retryTimer = null;
      this.openSse();
    }, this.retryMs);
    this.retryMs = Math.min(this.retryMs * 2, 15000);
  }

  private ingestRaw(raw: unknown, sseType: string): void {
    if (typeof raw !== 'string' || !raw.trim()) return;
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      return;
    }
    // Some servers wrap batches; accept arrays too.
    const list = Array.isArray(parsed) ? parsed : [parsed];
    for (const item of list) {
      const o = obj(item);
      // Named SSE event without a kind field: use the SSE event name and treat the payload as data.
      const withKind =
        !o.kind && !o.type && sseType !== 'message' ? { kind: sseType, data: o, seq: o.seq, ts: o.ts } : o;
      const ev = normEvent(withKind);
      if (ev) this.deliver(ev);
    }
  }

  private deliver(ev: AiraEvent): void {
    if (ev.seq > 0) {
      if (this.seen.has(ev.seq)) return;
      this.seen.add(ev.seq);
      if (this.seen.size > 5000) {
        const cutoff = this.lastSeq - 2500;
        for (const s of this.seen) if (s < cutoff) this.seen.delete(s);
      }
      if (ev.seq > this.lastSeq) this.lastSeq = ev.seq;
    }
    try {
      this.h.onEvent(ev);
    } catch (err) {
      console.error('[aira] event handler failed', ev, err);
    }
  }

  private startPolling(): void {
    if (this.polling || this.stopped) return;
    this.polling = true;
    this.h.onStatus(this.pollFailing ? 'offline' : 'polling');
    const tick = async () => {
      if (!this.polling) return;
      try {
        const r = await fetch(`${this.base}/api/events?after=${this.lastSeq}`, { cache: 'no-store', headers: authHeaders(this.base) });
        if (!r.ok) throw new Error(String(r.status));
        const body: unknown = await r.json();
        const list = Array.isArray(body) ? body : arr(obj(body).events);
        if (this.pollFailing) {
          this.pollFailing = false;
          this.h.onReconnect();
        }
        if (this.polling) this.h.onStatus('polling');
        for (const item of list) {
          const ev = normEvent(item);
          if (ev) this.deliver(ev);
        }
      } catch {
        this.pollFailing = true;
        if (this.polling) this.h.onStatus('offline');
      }
      if (this.polling) this.pollTimer = window.setTimeout(tick, 1500);
    };
    void tick();
  }

  private stopPolling(): void {
    this.polling = false;
    if (this.pollTimer !== null) clearTimeout(this.pollTimer);
    this.pollTimer = null;
  }
}
