// The Live page: the SAM 3.1 camera frame with its count boxes, the two-model check strip and the live
// conversation. Built once and kept alive (the feed must keep growing while another page is open).
import { checkCard, hhmm } from './pages';
import type { ConnStatus, CountInfo, Verification } from './types';
import { h, retrigger } from './util';

const MAX_FEED = 150;

export class LiveView {
  readonly root: HTMLElement;
  private readonly list: HTMLElement;
  private readonly empty: HTMLElement;
  private readonly cam: HTMLElement;
  private readonly view: HTMLElement;
  private readonly img: HTMLImageElement;
  private readonly canvas: HTMLCanvasElement;
  private readonly camEmpty: HTMLElement;
  private readonly result: HTMLElement;
  private readonly checksRow: HTMLElement;
  private readonly checksMeta: HTMLElement;
  private readonly barText: HTMLElement;
  private readonly statusPill: HTMLElement;
  private partial: { role: string; el: HTMLElement } | null = null;
  private count: CountInfo | null = null;
  private countKey = '';
  private anim = 0;

  constructor(private readonly frameSrc: (id: string) => string) {
    this.barText = h('p', { text: 'Nothing waiting.' });
    this.statusPill = h('span', { class: 'status-pill', role: 'status' }, h('i'), h('span', { text: 'Connecting…' }));
    const bar = h('div', { class: 'live-bar' }, h('h2', { text: 'Waiting for the owner’s yes' }), this.barText, this.statusPill);

    this.img = h('img', { alt: 'Last camera frame counted by SAM 3.1' });
    this.canvas = h('canvas', { 'aria-hidden': 'true' });
    this.camEmpty = h(
      'div',
      { class: 'cam-empty' },
      h('span', { class: 'cam-pulse', 'aria-hidden': 'true' }),
      h('h3', { text: 'Waiting for a delivery count…' }),
      h('p', { text: 'Ask AIRA to check a delivery. SAM 3.1 segments and counts every item it sees on camera.' }),
    );
    this.view = h('div', { class: 'cam-view' }, this.img, this.canvas, this.camEmpty);
    this.result = h('div', { class: 'cam-result', 'aria-live': 'polite' });
    this.cam = h(
      'section',
      { class: 'cam', 'aria-label': 'Camera count' },
      h('div', { class: 'cam-head' }, h('span', { class: 'cam-chip', text: 'Camera · SAM 3.1 count' })),
      this.view,
      this.result,
    );

    this.checksMeta = h('span');
    this.checksRow = h('div', { class: 'checks-row' });
    const strip = h(
      'section',
      { class: 'checks-strip' },
      h('div', { class: 'strip-head' }, h('h2', { text: 'Two-model verification' }), this.checksMeta),
      this.checksRow,
    );

    this.empty = h('p', { class: 'talk-empty', text: 'Nothing said yet.' });
    this.list = h('ol', { class: 'talk-list', 'aria-live': 'polite', 'aria-label': 'Conversation' });
    const talk = h(
      'section',
      { class: 'talk' },
      h('div', { class: 'talk-head' }, h('h2', { text: 'Conversation' }), h('span', { class: 'talk-models', text: 'Voice Transcribe → Muse Spark' })),
      this.empty,
      this.list,
    );

    this.root = h('div', { class: 'live-grid' }, bar, h('div', { class: 'cam-col' }, this.cam, strip), talk);

    this.img.addEventListener('load', () => {
      this.img.classList.add('ready');
      this.camEmpty.hidden = true;
      this.view.classList.add('has-frame');
      this.reveal();
    });
    this.img.addEventListener('error', () => {
      this.img.classList.remove('ready');
      this.placeholder('Frame image unavailable', 'The count is below; the camera frame could not be loaded.');
    });
    if (typeof ResizeObserver !== 'undefined') new ResizeObserver(() => this.draw(1)).observe(this.view);
    else window.addEventListener('resize', () => this.draw(1));
    this.renderResult('');
    this.setChecks([], false);
  }

  /** Called after the root is put back in the page. */
  attached(): void {
    this.draw(1);
    this.list.scrollTop = this.list.scrollHeight;
  }

  /* ---------------------------------------------------------------- bar */

  setBar(pending: number, prompt: string, status: ConnStatus): void {
    this.barText.replaceChildren(
      pending > 0
        ? h('span', { class: 'ask', text: `${pending} waiting${prompt ? ` — “${prompt}”` : ''}` })
        : 'Nothing waiting. AIRA asks the owner before every sale and stock change.',
    );
    const text: Record<ConnStatus, string> = {
      connecting: 'Connecting…',
      live: 'Connected',
      polling: 'Connected (polling)',
      offline: 'Offline',
      demo: 'Demo shop',
    };
    this.statusPill.className = `status-pill ${status === 'offline' ? 'off' : status === 'connecting' ? '' : 'on'}`;
    this.statusPill.lastElementChild!.textContent = text[status];
  }

  /* ---------------------------------------------------------------- conversation */

  addTranscript(role: string, text: string, final: boolean, ts: number | null): void {
    if (!text.trim()) return;
    const aira = /aira|assistant|agent|bot/i.test(role);
    const key = aira ? 'aira' : 'owner';
    let el: HTMLElement;
    if (this.partial && this.partial.role === key) {
      el = this.partial.el;
      el.querySelector('.say-text')!.textContent = text;
    } else {
      el = h(
        'li',
        { class: `say ${key}` },
        h('span', { class: 'say-face', 'aria-hidden': 'true', text: aira ? 'A' : 'O' }),
        h(
          'div',
          { class: 'say-bubble' },
          h('span', { class: 'say-who' }, h('span', { text: aira ? 'AIRA' : 'Shop owner' }), h('time', { text: hhmm(ts ?? Date.now()) })),
          h('p', { class: 'say-text', text }),
        ),
      );
      this.push(el);
    }
    el.classList.toggle('partial', !final);
    this.partial = final ? null : { role: key, el };
  }

  addSystem(text: string, tone: string): void {
    this.push(h('li', { class: `note ${tone}`, text }));
  }

  private push(node: HTMLElement): void {
    this.empty.hidden = true;
    const l = this.list;
    const atEnd = l.scrollHeight - l.scrollTop - l.clientHeight < 140;
    l.append(node);
    while (l.childElementCount > MAX_FEED) l.firstElementChild?.remove();
    if (atEnd || l.childElementCount < 4) l.scrollTo({ top: l.scrollHeight, behavior: 'smooth' });
  }

  /* ---------------------------------------------------------------- checks */

  setChecks(list: Verification[], fresh: boolean): void {
    const agreed = list.filter((v) => v.agreed).length;
    this.checksMeta.textContent = list.length ? `${agreed} of ${list.length} agreed` : 'Muse Spark × 2';
    this.checksRow.replaceChildren(
      ...(list.length
        ? list.slice(0, 12).map((v, i) => checkCard(v, fresh && i === 0))
        : [h('p', { class: 'blank', text: 'Cash and invoice readings appear here. Two models read each one on their own; AIRA only speaks a number when they agree.' })]),
    );
    this.checksRow.scrollLeft = 0;
  }

  /* ---------------------------------------------------------------- SAM count */

  setCount(c: CountInfo, productName: string): void {
    const key = `${c.frame_id ?? ''}|${c.sku}|${c.counted}|${c.expected ?? ''}|${c.boxes.length}`;
    if (key === this.countKey) return;
    this.countKey = key;
    this.count = c;
    this.renderResult(productName);
    if (c.frame_id) {
      this.img.classList.remove('ready');
      this.img.src = this.frameSrc(c.frame_id);
    } else {
      this.img.removeAttribute('src');
      this.img.classList.remove('ready');
      this.placeholder('No frame stored for this count', 'SAM 3.1 counted the items; the camera frame was not kept.');
    }
    retrigger(this.cam, 'pulse');
  }

  private placeholder(title: string, sub: string): void {
    this.view.classList.remove('has-frame');
    this.camEmpty.hidden = false;
    this.camEmpty.replaceChildren(h('h3', { text: title }), h('p', { text: sub }));
    this.canvas.getContext('2d')?.clearRect(0, 0, this.canvas.width, this.canvas.height);
  }

  private renderResult(productName: string): void {
    const c = this.count;
    if (!c) {
      this.result.replaceChildren(h('span', { class: 'meta', text: 'No count yet.' }));
      return;
    }
    const diff = c.expected === null ? null : c.counted - c.expected;
    const tone = diff === null ? '' : diff === 0 ? 'good' : 'bad';
    const verdict = diff === null ? '' : diff === 0 ? ' — all present' : diff < 0 ? ` — ${-diff} missing` : ` — ${diff} extra`;
    const scores = c.boxes.map((b) => b.score).filter((s): s is number => s !== null);
    const avg = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null;
    this.result.replaceChildren(
      h('span', { class: `big ${tone}`.trim(), text: `Counted ${c.counted}${c.expected !== null ? ` of ${c.expected}` : ''}${verdict}` }),
      h(
        'span',
        { class: 'meta', text: `${productName}${c.sku ? ` (${c.sku})` : ''} · ${c.boxes.length} boxes${avg !== null ? ` · avg confidence ${Math.round(avg * 100)}%` : ''}${c.ts ? ` · ${hhmm(c.ts)}` : ''}` },
      ),
    );
  }

  private reveal(): void {
    const id = ++this.anim;
    const start = performance.now();
    const step = (t: number) => {
      if (id !== this.anim) return;
      const k = Math.min(1, (t - start) / 1100);
      this.draw(k);
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  private draw(progress: number): void {
    const { img, canvas, count: c } = this;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const W = img.clientWidth;
    const H = img.clientHeight;
    if (!c || !img.classList.contains('ready') || !W || !H) {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      return;
    }
    const dpr = window.devicePixelRatio || 1;
    Object.assign(canvas.style, { left: `${img.offsetLeft}px`, top: `${img.offsetTop}px`, width: `${W}px`, height: `${H}px` });
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);

    const sx = W / (c.w || img.naturalWidth || W);
    const sy = H / (c.h || img.naturalHeight || H);
    if (progress < 1) {
      const y = H * progress;
      const g = ctx.createLinearGradient(0, y - 44, 0, y);
      g.addColorStop(0, 'rgba(124,58,237,0)');
      g.addColorStop(1, 'rgba(124,58,237,0.35)');
      ctx.fillStyle = g;
      ctx.fillRect(0, y - 44, W, 44);
      ctx.fillStyle = 'rgba(196,181,253,0.95)';
      ctx.fillRect(0, y - 1, W, 2);
    }
    const n = c.boxes.length;
    const stroke = '#22C55E';
    c.boxes.forEach((b, i) => {
      const at = n <= 1 ? 0 : (i / n) * 0.85;
      if (progress < at) return;
      ctx.globalAlpha = Math.min(1, (progress - at) / 0.15);
      const x = b.left * sx;
      const y = b.top * sy;
      const w = Math.max(2, (b.right - b.left) * sx);
      const bh = Math.max(2, (b.bottom - b.top) * sy);
      ctx.lineWidth = 2.5;
      ctx.strokeStyle = stroke;
      ctx.fillStyle = 'rgba(34,197,94,0.14)';
      rounded(ctx, x, y, w, bh, 6);
      ctx.fill();
      ctx.stroke();
      const label = String(i + 1);
      ctx.font = '700 13px Inter, system-ui, sans-serif';
      const tw = Math.max(20, ctx.measureText(label).width + 10);
      ctx.fillStyle = stroke;
      rounded(ctx, x, y - 1, tw, 19, 5);
      ctx.fill();
      ctx.fillStyle = '#052e14';
      ctx.fillText(label, x + 5, y + 13);
      ctx.globalAlpha = 1;
    });
  }
}

function rounded(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, hh: number, r: number): void {
  const rr = Math.min(r, w / 2, hh / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + hh, rr);
  ctx.arcTo(x + w, y + hh, x, y + hh, rr);
  ctx.arcTo(x, y + hh, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}
