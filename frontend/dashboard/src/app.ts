import type { AiraEvent, ConnStatus, CountInfo, Delivery, Product, Sale, Snapshot, Verification } from './types';
import {
  $,
  type Dict,
  clock,
  h,
  isToday,
  normCount,
  normDelivery,
  normSale,
  normVerification,
  num,
  pick,
  retrigger,
  rupees,
  str,
} from './util';

const MAX_FEED = 120;
const MAX_VERIFS = 30;
const DONE_STATES = /^(committed|done|ok|yes|no|rejected|expired|cancel+ed|resolved|answered|confirmed|declined|timeout)$/i;
const VOID_SALE = /cancel|reject|void|abandon|expired/i;

export interface AppOptions {
  frameSrc(frameId: string): string;
  /** Ask for a fresh snapshot soon (debounced by the caller). */
  requestRefresh(): void;
}

interface FeedPartial {
  role: string;
  el: HTMLElement;
}

export class App {
  private products = new Map<string, Product>();
  private productOrder: string[] = [];
  private sales = new Map<string, Sale>();
  private deliveries = new Map<string, Delivery>();
  private lastCount: CountInfo | null = null;
  private countKey = '';
  private pendingBase = 0;
  private pendingLocal = new Map<string, string>();
  private pendingPrompt = '';
  private mock = false;
  private hasSnapshot = false;
  private verifs: Verification[] = [];
  private partial: FeedPartial | null = null;
  private stockRows = new Map<string, HTMLTableRowElement>();
  private kpiValues: Record<string, number> = {};
  private boxAnim = 0;

  constructor(private readonly opts: AppOptions) {
    this.startClock();
    const img = $('frame-img') as HTMLImageElement;
    img.addEventListener('load', () => {
      img.classList.add('ready');
      $('frame-empty').hidden = true;
      this.animateBoxes();
    });
    img.addEventListener('error', () => {
      img.classList.remove('ready');
      this.showFramePlaceholder('Frame image unavailable');
    });
    if (typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(() => this.drawBoxes(1)).observe($('frame-wrap'));
    } else {
      window.addEventListener('resize', () => this.drawBoxes(1));
    }
    this.renderAll();
  }

  // ---------------------------------------------------------------- status

  setStatus(s: ConnStatus): void {
    const el = $('live');
    el.className = `live ${s}`;
    const label: Record<ConnStatus, string> = {
      connecting: 'CONNECTING',
      live: 'LIVE',
      polling: 'LIVE · POLLING',
      offline: 'OFFLINE',
      demo: 'LIVE · DEMO',
    };
    el.querySelector('b')!.textContent = label[s];
  }

  // ---------------------------------------------------------------- snapshot

  applySnapshot(s: Snapshot): void {
    const nextOrder: string[] = [];
    const next = new Map<string, Product>();
    for (const p of s.products) {
      next.set(p.sku, p);
      nextOrder.push(p.sku);
    }
    this.products = next;
    this.productOrder = nextOrder;

    // Snapshot sales are authoritative but may be a "recent" window: merge, don't drop live ones.
    for (const sale of s.sales) this.sales.set(sale.id, sale);
    for (const d of s.deliveries) this.deliveries.set(d.id, d);
    if (s.last_count) this.setCount(s.last_count);
    this.pendingBase = s.pending_confirms;
    this.pendingLocal.clear();
    if (s.pending_confirms === 0) this.pendingPrompt = '';
    this.mock = s.mock;
    this.hasSnapshot = true;
    this.renderAll();
  }

  // ---------------------------------------------------------------- events

  applyEvent(ev: AiraEvent): void {
    const d = ev.data;
    switch (ev.kind) {
      case 'transcript':
        this.addTranscript(str(pick(d, 'role', 'speaker'), 'user'), str(d.text), d.final !== false, ev.ts);
        break;
      case 'say':
        // The aira transcript event already carries the spoken text; ignore duplicates.
        break;
      case 'sale': {
        const sale = normSale(d);
        if (sale) {
          if (sale.ts === null) sale.ts = ev.ts ?? Date.now();
          const prev = this.sales.get(sale.id);
          this.sales.set(sale.id, { ...prev, ...sale, items: sale.items.length ? sale.items : (prev?.items ?? []) });
          this.addSystem(`Sale ${sale.id} · ${rupees(sale.total)} · ${sale.state}`, 'sale');
        }
        this.renderKpis();
        this.renderSales();
        this.opts.requestRefresh();
        break;
      }
      case 'payment': {
        const saleId = str(pick(d, 'sale_id', 'sale'));
        const amount = num(pick(d, 'amount', 'total'), null);
        const method = str(pick(d, 'method', 'type'));
        const sale = saleId ? this.sales.get(saleId) : undefined;
        if (sale) {
          sale.state = str(pick(d, 'sale_state'), 'paid');
          if (method) sale.method = method;
        }
        const change = num(d.change, null);
        this.addSystem(
          `Payment${method ? ` · ${method.toUpperCase()}` : ''}${amount !== null ? ` · ${rupees(amount)}` : ''}${
            change ? ` · change ${rupees(change)}` : ''
          }`,
          'payment',
        );
        this.renderKpis();
        this.renderSales();
        this.opts.requestRefresh();
        break;
      }
      case 'ledger':
      case 'stock':
      case 'inventory': {
        const sku = str(d.sku);
        const onHand = num(pick(d, 'on_hand', 'stock', 'balance'), null);
        const p = sku ? this.products.get(sku) : undefined;
        if (p && onHand !== null) {
          p.stock = onHand;
          this.renderStock();
        }
        this.opts.requestRefresh();
        break;
      }
      case 'delivery': {
        const del = normDelivery(d);
        if (del) {
          this.deliveries.set(del.id, { ...this.deliveries.get(del.id), ...del });
          this.renderDeliveries();
        }
        this.opts.requestRefresh();
        break;
      }
      case 'count': {
        const c = normCount(d);
        if (c) {
          if (c.ts === null) c.ts = ev.ts;
          this.setCount(c);
          this.addSystem(`SAM 3.1 counted ${c.counted} × ${this.productName(c.sku)}`, 'count');
        }
        break;
      }
      case 'confirm':
        this.onConfirm(d);
        break;
      case 'verification': {
        const v = normVerification(d, ev.ts);
        this.verifs.unshift(v);
        this.verifs.length = Math.min(this.verifs.length, MAX_VERIFS);
        this.renderVerifs(true);
        break;
      }
      default:
        if (ev.kind === 'state' && str(d.entity)) {
          // contracts.State {entity,id,state}: patch known entities.
          const ent = normKindLite(str(d.entity));
          const id = str(d.id);
          if (ent === 'sale' && this.sales.has(id)) this.sales.get(id)!.state = str(d.state);
          if (ent === 'delivery' && this.deliveries.has(id)) this.deliveries.get(id)!.state = str(d.state);
          this.renderSales();
          this.renderDeliveries();
          this.renderKpis();
          this.opts.requestRefresh();
        }
    }
  }

  private onConfirm(d: Dict): void {
    const id = str(pick(d, 'id', 'confirm_id')) || `anon-${Date.now()}`;
    const st = str(pick(d, 'state', 'status', 'answer'));
    if (st && DONE_STATES.test(st)) {
      if (this.pendingLocal.has(id)) this.pendingLocal.delete(id);
      else this.pendingBase = Math.max(0, this.pendingBase - 1);
      const ok = /committed|yes|ok|done|confirmed/i.test(st);
      this.addSystem(`Owner ${ok ? 'confirmed' : 'declined'}${d.prompt ? `: ${str(d.prompt)}` : ''}`, ok ? 'ok' : 'warn');
      if (this.pendingCount() === 0) this.pendingPrompt = '';
    } else {
      if (!this.pendingLocal.has(id)) this.pendingLocal.set(id, str(d.prompt));
      this.pendingPrompt = str(d.prompt) || this.pendingPrompt;
      this.addSystem(`Awaiting owner's "yes": ${str(d.prompt) || 'confirmation'}`, 'confirm');
    }
    this.renderKpis();
    this.opts.requestRefresh();
  }

  private pendingCount(): number {
    return Math.max(0, this.pendingBase + this.pendingLocal.size);
  }

  private productName(sku: string): string {
    return this.products.get(sku)?.name ?? (sku || 'items');
  }

  // ---------------------------------------------------------------- rendering

  private renderAll(): void {
    $('mock').hidden = !this.mock;
    this.renderKpis();
    this.renderStock();
    this.renderSales();
    this.renderDeliveries();
    this.renderVerifs(false);
    this.renderCountText();
    if (!this.hasSnapshot && $('feed').childElementCount === 0) {
      $('feed').append(h('div', { class: 'empty-note', text: 'Waiting for the shop owner to speak…' }));
    }
  }

  private todaysSales(): Sale[] {
    return [...this.sales.values()].filter((s) => isToday(s.ts) && !VOID_SALE.test(s.state));
  }

  private renderKpis(): void {
    const sales = this.todaysSales();
    const revenue = sales.reduce((s, x) => s + x.total, 0);
    const items = sales.reduce((s, x) => s + x.items.reduce((a, i) => a + i.qty, 0), 0);
    const paid = sales.filter((s) => /paid|committed|done|complete/i.test(s.state)).length;
    this.setKpi('k-revenue', revenue, rupees);
    this.setKpi('k-sales', sales.length, String);
    this.setKpi('k-items', items, String);
    $('k-revenue-sub').textContent = sales.length ? `avg ticket ${rupees(revenue / sales.length)}` : 'no sales yet today';
    $('k-sales-sub').textContent = `${paid} paid · ${sales.length - paid} open`;
    const low = [...this.products.values()].filter((p) => p.stock <= p.reorder_threshold).length;
    $('k-items-sub').textContent = low ? `${low} product${low === 1 ? '' : 's'} low on stock` : 'stock healthy';

    const pending = this.pendingCount();
    this.setKpi('k-pending', pending, String);
    const card = $('k-pending-card');
    card.classList.toggle('waiting', pending > 0);
    $('k-pending-sub').textContent =
      pending > 0 ? (this.pendingPrompt ? `“${this.pendingPrompt}”` : 'waiting for owner’s yes / no') : 'nothing waiting';
  }

  private setKpi(id: string, value: number, fmt: (n: number) => string): void {
    const el = $(id);
    const prev = this.kpiValues[id];
    this.kpiValues[id] = value;
    if (prev === undefined || prev === value) {
      el.textContent = fmt(value);
      return;
    }
    retrigger(el.closest('.kpi') ?? el, 'bump');
    const start = performance.now();
    const dur = 700;
    const step = (t: number) => {
      if (this.kpiValues[id] !== value) return;
      const k = Math.min(1, (t - start) / dur);
      const e = 1 - Math.pow(1 - k, 3);
      el.textContent = fmt(Math.round(prev + (value - prev) * e));
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  private renderStock(): void {
    const body = $('stock-body');
    const seen = new Set<string>();
    let lowCount = 0;
    this.productOrder.forEach((sku, idx) => {
      const p = this.products.get(sku);
      if (!p) return;
      seen.add(sku);
      const low = p.stock <= p.reorder_threshold;
      const out = p.stock <= 0;
      if (low) lowCount++;
      let row = this.stockRows.get(sku);
      if (!row) {
        row = h(
          'tr',
          { 'data-sku': sku },
          h('td', null, h('div', { class: 'pname' }), h('div', { class: 'psku' })),
          h('td', { class: 'num price' }),
          h('td', { class: 'num qty' }, h('span', { class: 'qtyv' }), h('span', { class: 'delta' })),
          h('td', null, h('div', { class: 'bar' }, h('i'))),
          h('td', null, h('span', { class: 'pill' })),
        );
        row.dataset.stock = String(p.stock);
        this.stockRows.set(sku, row);
      }
      if (body.children[idx] !== row) body.insertBefore(row, body.children[idx] ?? null);
      row.querySelector('.pname')!.textContent = p.name;
      row.querySelector('.psku')!.textContent = sku;
      row.querySelector('.price')!.textContent = rupees(p.price);
      row.querySelector('.qtyv')!.textContent = String(p.stock);
      const cap = Math.max(p.reorder_threshold * 3, p.stock, 1);
      (row.querySelector('.bar i') as HTMLElement).style.width = `${Math.min(100, (p.stock / cap) * 100)}%`;
      const pill = row.querySelector('.pill')!;
      pill.textContent = out ? 'OUT' : low ? 'LOW' : 'OK';
      pill.className = `pill ${out ? 'out' : low ? 'low' : 'ok'}`;
      row.classList.toggle('is-low', low);

      const prev = Number(row.dataset.stock);
      if (Number.isFinite(prev) && prev !== p.stock) {
        const diff = p.stock - prev;
        const delta = row.querySelector('.delta') as HTMLElement;
        delta.textContent = diff > 0 ? `+${diff}` : `−${Math.abs(diff)}`;
        delta.className = `delta ${diff > 0 ? 'up' : 'down'}`;
        retrigger(delta, 'show');
        row.classList.remove('flash-up', 'flash-down');
        retrigger(row, diff > 0 ? 'flash-up' : 'flash-down');
      }
      row.dataset.stock = String(p.stock);
    });
    for (const [sku, row] of this.stockRows) {
      if (!seen.has(sku)) {
        row.remove();
        this.stockRows.delete(sku);
      }
    }
    $('stock-meta').textContent = this.products.size
      ? `${this.products.size} products${lowCount ? ` · ${lowCount} low` : ''}`
      : '';
    $('stock-empty').hidden = this.products.size > 0;
  }

  private renderSales(): void {
    const list = $('sales-list');
    const sales = [...this.sales.values()].sort((a, b) => (b.ts ?? 0) - (a.ts ?? 0)).slice(0, 12);
    list.replaceChildren(
      ...sales.map((s) => {
        const items = s.items.map((i) => `${i.qty}× ${i.name || this.productName(i.sku)}`).join(', ') || '—';
        const cls = VOID_SALE.test(s.state) ? 'void' : /paid|committed|done|complete/i.test(s.state) ? 'ok' : 'open';
        return h(
          'div',
          { class: 'sale-row' },
          h('div', { class: 'sale-main' }, h('div', { class: 'sale-items', text: items }), h('div', { class: 'sale-meta', text: `${s.id}${s.method ? ` · ${s.method.toUpperCase()}` : ''} · ${clock(s.ts)}` })),
          h('div', { class: 'sale-right' }, h('div', { class: 'sale-total', text: rupees(s.total) }), h('span', { class: `pill ${cls}`, text: s.state })),
        );
      }),
    );
    if (!sales.length) list.append(h('div', { class: 'empty-note', text: 'No sales yet today' }));
  }

  private renderDeliveries(): void {
    const el = $('deliveries');
    const dels = [...this.deliveries.values()].sort((a, b) => (b.ts ?? 0) - (a.ts ?? 0)).slice(0, 4);
    el.replaceChildren(
      ...dels.map((d) => {
        const disc = d.discrepancy ?? (d.counted !== null && d.expected !== null ? d.counted - d.expected : null);
        const bad = disc !== null && disc !== 0;
        return h(
          'div',
          { class: `del ${bad ? 'bad' : 'good'}` },
          h('b', { text: this.productName(d.sku) }),
          h('span', { text: `${d.counted ?? '?'} / ${d.expected ?? '?'}` }),
          disc !== null && disc !== 0 ? h('em', { text: disc < 0 ? `${-disc} short` : `${disc} extra` }) : h('em', { text: '✓' }),
          h('small', { text: d.state }),
        );
      }),
    );
  }

  private renderVerifs(newest: boolean): void {
    const el = $('verifs');
    el.replaceChildren(
      ...this.verifs.map((v, i) => {
        const card = h(
          'div',
          { class: `verif ${v.agreed ? 'agree' : 'disagree'}${newest && i === 0 ? ' enter' : ''}` },
          h(
            'div',
            { class: 'verif-head' },
            h('span', { class: `vtype ${v.type}`, text: v.type === 'invoice' ? 'INVOICE' : v.type === 'cash' ? 'CASH' : v.type.toUpperCase() }),
            h('span', { class: 'vtotal', text: v.total !== null ? rupees(v.total) : '—' }),
            h('span', { class: `badge ${v.agreed ? 'match' : 'mismatch'}`, text: v.agreed ? '✓ MATCH' : '✕ MISMATCH' }),
            h('span', { class: 'vtime', text: clock(v.ts) }),
          ),
          h(
            'div',
            { class: 'readers' },
            ...v.readers.map((r) =>
              h('div', { class: 'reader' }, h('span', { class: 'rname', text: r.name }), h('span', { class: 'rval', text: r.value }), r.detail ? h('span', { class: 'rdet', text: r.detail }) : null),
            ),
          ),
          v.note ? h('div', { class: 'vnote', text: v.note }) : null,
        );
        return card;
      }),
    );
    if (!this.verifs.length) el.append(h('div', { class: 'empty-note', text: 'Cash and invoice readings appear here. Two Meta models read each one independently; AIRA only speaks a number when they agree.' }));
    const agree = this.verifs.filter((v) => v.agreed).length;
    $('verif-meta').textContent = this.verifs.length ? `${agree}/${this.verifs.length} agreed` : '';
  }

  // ---------------------------------------------------------------- feed

  private addTranscript(role: string, text: string, final: boolean, ts: number | null): void {
    if (!text.trim()) return;
    const isAira = /aira|assistant|agent|bot/i.test(role);
    const roleKey = isAira ? 'aira' : 'user';
    const feed = $('feed');
    feed.querySelector('.empty-note')?.remove();
    let bubble: HTMLElement;
    if (this.partial && this.partial.role === roleKey) {
      bubble = this.partial.el;
      bubble.querySelector('.btext')!.textContent = text;
    } else {
      bubble = h(
        'div',
        { class: `msg ${roleKey}` },
        h('div', { class: 'avatar', text: isAira ? 'A' : 'O' }),
        h(
          'div',
          { class: 'bubble' },
          h('div', { class: 'bwho' }, h('span', { text: isAira ? 'AIRA' : 'Shop owner' }), h('time', { text: clock(ts ?? Date.now()) })),
          h('div', { class: 'btext', text }),
        ),
      );
      this.append(bubble);
    }
    bubble.classList.toggle('partial', !final);
    this.partial = final ? null : { role: roleKey, el: bubble };
  }

  private addSystem(text: string, tone: string): void {
    this.append(h('div', { class: `sys ${tone}` }, h('span', { text })));
  }

  private append(node: HTMLElement): void {
    const feed = $('feed');
    feed.querySelector('.empty-note')?.remove();
    const nearBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 120;
    feed.append(node);
    while (feed.childElementCount > MAX_FEED) feed.firstElementChild?.remove();
    if (nearBottom || feed.childElementCount < 4) feed.scrollTo({ top: feed.scrollHeight, behavior: 'smooth' });
  }

  // ---------------------------------------------------------------- SAM count

  private setCount(c: CountInfo): void {
    const key = `${c.frame_id ?? ''}|${c.sku}|${c.counted}|${c.expected ?? ''}|${c.boxes.length}`;
    if (key === this.countKey) return;
    this.countKey = key;
    this.lastCount = c;
    this.renderCountText();
    const img = $('frame-img') as HTMLImageElement;
    if (c.frame_id) {
      img.classList.remove('ready');
      img.src = this.opts.frameSrc(c.frame_id);
      $('frame-empty').hidden = true;
    } else {
      img.removeAttribute('src');
      img.classList.remove('ready');
      this.showFramePlaceholder('No frame stored for this count');
    }
    retrigger($('count-panel'), 'pulse');
  }

  private showFramePlaceholder(msg: string): void {
    const e = $('frame-empty');
    e.hidden = false;
    e.textContent = msg;
    const c = $('frame-canvas') as HTMLCanvasElement;
    c.getContext('2d')?.clearRect(0, 0, c.width, c.height);
  }

  private renderCountText(): void {
    const el = $('count-result');
    const c = this.lastCount;
    if (!c) {
      el.className = 'count-result idle';
      el.replaceChildren(h('div', { class: 'cr-line', text: 'No count yet' }), h('div', { class: 'cr-sub', text: 'Ask AIRA to check a delivery — SAM 3.1 counts every item it sees.' }));
      return;
    }
    const diff = c.expected === null ? null : c.counted - c.expected;
    const tone = diff === null ? 'neutral' : diff === 0 ? 'good' : 'bad';
    const verdict = diff === null ? '' : diff === 0 ? 'all present' : diff < 0 ? `${-diff} missing` : `${diff} extra`;
    const scores = c.boxes.map((b) => b.score).filter((s): s is number => s !== null);
    const avg = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null;
    el.className = `count-result ${tone}`;
    el.replaceChildren(
      h(
        'div',
        { class: 'cr-line' },
        h('span', { class: 'lbl', text: 'Counted ' }),
        h('b', { text: String(c.counted) }),
        c.expected !== null ? h('span', { class: 'lbl', text: ' / Expected ' }) : null,
        c.expected !== null ? h('b', { class: 'exp', text: String(c.expected) }) : null,
        verdict ? h('span', { class: 'verdict', text: ` — ${verdict}` }) : null,
      ),
      h(
        'div',
        { class: 'cr-sub', text: `${this.productName(c.sku)}${c.sku ? ` (${c.sku})` : ''} · ${c.boxes.length} boxes${avg !== null ? ` · avg confidence ${(avg * 100).toFixed(0)}%` : ''}${c.ts ? ` · ${clock(c.ts)}` : ''}` },
      ),
    );
  }

  private animateBoxes(): void {
    const id = ++this.boxAnim;
    const start = performance.now();
    const dur = 1100;
    const step = (t: number) => {
      if (id !== this.boxAnim) return;
      const k = Math.min(1, (t - start) / dur);
      this.drawBoxes(k);
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  private drawBoxes(progress: number): void {
    const img = $('frame-img') as HTMLImageElement;
    const canvas = $('frame-canvas') as HTMLCanvasElement;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const c = this.lastCount;
    const W = img.clientWidth;
    const H = img.clientHeight;
    if (!c || !img.classList.contains('ready') || !W || !H) {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      return;
    }
    const dpr = window.devicePixelRatio || 1;
    canvas.style.left = `${img.offsetLeft}px`;
    canvas.style.top = `${img.offsetTop}px`;
    canvas.style.width = `${W}px`;
    canvas.style.height = `${H}px`;
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);

    // Boxes are in source-frame pixels. Prefer the count's declared frame size, else the JPEG's.
    const srcW = c.w || img.naturalWidth || W;
    const srcH = c.h || img.naturalHeight || H;
    const sx = W / srcW;
    const sy = H / srcH;
    const n = c.boxes.length;
    const css = getComputedStyle(document.documentElement);
    const good = css.getPropertyValue('--box').trim() || '#3ee6a8';

    // scan line sweeping down during the reveal
    if (progress < 1) {
      const y = H * progress;
      const g = ctx.createLinearGradient(0, y - 40, 0, y);
      g.addColorStop(0, 'rgba(0,129,251,0)');
      g.addColorStop(1, 'rgba(0,129,251,0.35)');
      ctx.fillStyle = g;
      ctx.fillRect(0, y - 40, W, 40);
      ctx.fillStyle = 'rgba(120,190,255,0.9)';
      ctx.fillRect(0, y - 1, W, 2);
    }

    c.boxes.forEach((b, i) => {
      const appearAt = n <= 1 ? 0 : (i / n) * 0.85;
      if (progress < appearAt) return;
      const local = Math.min(1, (progress - appearAt) / 0.15);
      const x = b.left * sx;
      const y = b.top * sy;
      const w = Math.max(2, (b.right - b.left) * sx);
      const hh = Math.max(2, (b.bottom - b.top) * sy);
      ctx.globalAlpha = local;
      ctx.lineWidth = 2.5;
      ctx.strokeStyle = good;
      ctx.fillStyle = 'rgba(62,230,168,0.12)';
      roundRect(ctx, x, y, w, hh, 6);
      ctx.fill();
      ctx.stroke();
      // number tag
      const label = String(i + 1);
      ctx.font = '700 13px Inter, ui-sans-serif, system-ui, sans-serif';
      const tw = ctx.measureText(label).width + 10;
      ctx.fillStyle = good;
      roundRect(ctx, x, y - 1, Math.max(20, tw), 19, 5);
      ctx.fill();
      ctx.fillStyle = '#04140d';
      ctx.fillText(label, x + 5, y + 13);
      ctx.globalAlpha = 1;
    });
  }

  // ---------------------------------------------------------------- misc

  private startClock(): void {
    const tick = () => {
      $('clock').textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    };
    tick();
    window.setInterval(tick, 1000);
  }
}

function normKindLite(s: string): string {
  const k = s.toLowerCase();
  if (k.endsWith('ies')) return k.slice(0, -3) + 'y';
  return k.endsWith('s') ? k.slice(0, -1) : k;
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number): void {
  const rr = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}
