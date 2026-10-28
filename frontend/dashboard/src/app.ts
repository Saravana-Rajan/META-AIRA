// The dashboard controller: holds the shop state fed by the backend (snapshot + events) or the demo,
// draws the frame (menu, top bar) and renders the open page. Pages: Overview, Today, Stock, Prices,
// Orders & deliveries, Live.
import { icon, type IconName } from './icons';
import { LiveView } from './live';
import { hhmm, ordersPage, overviewPage, pricesPage, stockPage, todayPage, type PageData, type PageOut } from './pages';
import type { AiraEvent, ConnStatus, Delivery, Product, Sale, Snapshot, Verification } from './types';
import { type Dict, h, normCount, normDelivery, normSale, normVerification, num, pick, rupees, str } from './util';

const MAX_VERIFS = 30;
const DONE_STATES = /^(committed|done|ok|yes|no|rejected|expired|cancel+ed|resolved|answered|confirmed|declined|timeout)$/i;

export interface AppOptions {
  frameSrc(frameId: string): string;
  /** Ask for a fresh snapshot soon (debounced by the caller). */
  requestRefresh(): void;
}

interface Route {
  hash: string;
  label: string;
  icon: IconName;
  build?: (d: PageData) => PageOut;
}

const ROUTES: Route[] = [
  { hash: '#/', label: 'Overview', icon: 'overview', build: overviewPage },
  { hash: '#/today', label: 'Today', icon: 'today', build: todayPage },
  { hash: '#/stock', label: 'Stock', icon: 'stock', build: stockPage },
  { hash: '#/prices', label: 'Prices', icon: 'prices', build: pricesPage },
  { hash: '#/orders', label: 'Orders & deliveries', icon: 'orders', build: ordersPage },
  { hash: '#/live', label: 'Live', icon: 'live' },
];

const RAIL_KEY = 'aira.dashboard.rail';

function routeFor(hash: string): Route | null {
  if (hash === '' || hash === '#') return ROUTES[0]!;
  if (!hash.startsWith('#/')) return null;
  return ROUTES.find((r) => r.hash === hash) ?? ROUTES[0]!;
}

export class App {
  private products = new Map<string, Product>();
  private sales = new Map<string, Sale>();
  private deliveries = new Map<string, Delivery>();
  private verifs: Verification[] = [];
  private pendingBase = 0;
  private pendingLocal = new Map<string, string>();
  private pendingPrompt = '';
  private mock = false;
  private hasSnapshot = false;
  private status: ConnStatus = 'connecting';
  private updatedAt: number | null = null;
  private changed = new Set<string>();
  private prevStock = new Map<string, number>();
  private prevTiles = new Map<string, string>();

  private route: Route = routeFor(location.hash) ?? ROUTES[0]!;
  private readonly live: LiveView;
  private readonly frame: HTMLElement;
  private readonly page: HTMLElement;
  private readonly title: HTMLElement;
  private readonly conn: HTMLElement;
  private readonly mockTag: HTMLElement;
  private readonly links = new Map<string, HTMLAnchorElement>();
  private rafId = 0;

  constructor(private readonly opts: AppOptions) {
    this.live = new LiveView(opts.frameSrc);

    let rail = false;
    try {
      rail = localStorage.getItem(RAIL_KEY) === '1';
    } catch {
      /* storage blocked */
    }

    const logoSrc = `${import.meta.env.BASE_URL}aira-logo-192.png`;
    const nav = h(
      'ul',
      { class: 'menu-links' },
      ...ROUTES.map((r) => {
        const a = h('a', { class: 'menu-link', href: r.hash, title: r.label }, icon(r.icon), h('span', { class: 'menu-text', text: r.label }));
        this.links.set(r.hash, a);
        return h('li', null, a);
      }),
    );
    const fold = h('button', { class: 'menu-fold', type: 'button', 'aria-controls': 'menu' });
    const menu = h(
      'aside',
      { class: 'menu', id: 'menu' },
      h('div', { class: 'menu-brand' }, h('img', { src: logoSrc, alt: '', width: 36, height: 36 }), h('span', { class: 'wordmark', text: 'AIRA' })),
      h('nav', { 'aria-label': 'Dashboard sections' }, nav),
      h(
        'section',
        { class: 'meta-box', 'aria-label': 'Powered by Meta' },
        h('h2', null, 'Powered by ', h('b', { text: 'Meta' })),
        h('ul', { class: 'meta-list' }, h('li', { text: 'Muse Spark' }), h('li', { text: 'Voice Transcribe' }), h('li', { text: 'SAM 3.1' })),
      ),
      fold,
    );

    this.title = h('h1', { text: this.route.label });
    this.conn = h('div', { class: 'conn' });
    this.mockTag = h('span', { class: 'mock-tag', text: 'MOCK MODE', hidden: true });
    const burger = h('button', { class: 'burger', type: 'button', 'aria-controls': 'menu', 'aria-expanded': 'false', 'aria-label': 'Sections menu' }, icon('menu', 22, 2));
    const top = h('header', { class: 'top' }, burger, h('img', { class: 'top-logo', src: logoSrc, alt: 'AIRA', width: 40, height: 40 }), this.title, this.mockTag, this.conn);
    this.page = h('main', { class: 'page', id: 'main', tabindex: -1 });
    const scrim = h('div', { class: 'scrim', hidden: true, 'aria-hidden': 'true' });
    this.frame = h('div', { class: `frame${rail ? ' is-rail' : ''}` }, menu, scrim, h('div', { class: 'body' }, top, this.page));

    const setRail = (on: boolean) => {
      this.frame.classList.toggle('is-rail', on);
      fold.replaceChildren(icon(on ? 'right' : 'left', 20, 1.8));
      fold.setAttribute('aria-label', on ? 'Expand menu' : 'Collapse menu');
      try {
        localStorage.setItem(RAIL_KEY, on ? '1' : '0');
      } catch {
        /* ignore */
      }
    };
    setRail(rail);
    fold.addEventListener('click', () => setRail(!this.frame.classList.contains('is-rail')));
    const setDrawer = (open: boolean) => {
      this.frame.classList.toggle('menu-open', open);
      scrim.hidden = !open;
      burger.setAttribute('aria-expanded', String(open));
      burger.replaceChildren(icon(open ? 'close' : 'menu', 22, 2));
    };
    burger.addEventListener('click', () => setDrawer(!this.frame.classList.contains('menu-open')));
    scrim.addEventListener('click', () => setDrawer(false));

    window.addEventListener('hashchange', () => {
      const r = routeFor(location.hash);
      if (!r) return;
      setDrawer(false);
      this.route = r;
      this.renderNow();
      window.scrollTo({ top: 0 });
      this.page.focus({ preventScroll: true });
    });

    document.getElementById('root')!.replaceChildren(this.frame);
    window.setInterval(() => this.renderConn(), 30_000);
    this.renderNow();
  }

  // ---------------------------------------------------------------- inputs

  setStatus(s: ConnStatus): void {
    const was = this.status;
    this.status = s;
    this.renderConn();
    this.renderLiveBar();
    if (!this.hasSnapshot && was !== s) this.renderNow();
  }

  applySnapshot(s: Snapshot): void {
    this.products = new Map(s.products.map((p) => [p.sku, p]));
    // Snapshot sales may be a recent window: merge, never drop live ones.
    for (const sale of s.sales) this.sales.set(sale.id, sale);
    for (const d of s.deliveries) this.deliveries.set(d.id, { ...this.deliveries.get(d.id), ...d });
    if (s.last_count) this.live.setCount(s.last_count, this.nameOf(s.last_count.sku));
    this.pendingBase = s.pending_confirms;
    this.pendingLocal.clear();
    if (s.pending_confirms === 0) this.pendingPrompt = '';
    this.mock = s.mock;
    this.hasSnapshot = true;
    this.touched();
  }

  applyEvent(ev: AiraEvent): void {
    const d = ev.data;
    switch (ev.kind) {
      case 'transcript':
        this.live.addTranscript(str(pick(d, 'role', 'speaker'), 'user'), str(d.text), d.final !== false, ev.ts);
        return;
      case 'say':
        return; // the transcript event already carries AIRA's words
      case 'sale': {
        const sale = normSale(d);
        if (sale) {
          if (sale.ts === null) sale.ts = ev.ts ?? Date.now();
          const prev = this.sales.get(sale.id);
          this.sales.set(sale.id, { ...prev, ...sale, items: sale.items.length ? sale.items : (prev?.items ?? []) });
          this.live.addSystem(`Sale ${sale.id} · ${rupees(sale.total)} · ${sale.state}`, 'sale');
        }
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
        this.live.addSystem(
          `Payment${method ? ` · ${method.toUpperCase()}` : ''}${amount !== null ? ` · ${rupees(amount)}` : ''}${change ? ` · change ${rupees(change)}` : ''}`,
          'payment',
        );
        this.opts.requestRefresh();
        break;
      }
      case 'ledger':
      case 'stock':
      case 'inventory': {
        const p = this.products.get(str(d.sku));
        const onHand = num(pick(d, 'on_hand', 'stock', 'balance'), null);
        if (p && onHand !== null) p.stock = onHand;
        this.opts.requestRefresh();
        break;
      }
      case 'delivery': {
        const del = normDelivery(d);
        if (del) this.deliveries.set(del.id, { ...this.deliveries.get(del.id), ...del });
        this.opts.requestRefresh();
        break;
      }
      case 'count': {
        const c = normCount(d);
        if (c) {
          if (c.ts === null) c.ts = ev.ts;
          this.live.setCount(c, this.nameOf(c.sku));
          this.live.addSystem(`SAM 3.1 counted ${c.counted} × ${this.nameOf(c.sku)}`, 'count');
        }
        return;
      }
      case 'confirm':
        this.onConfirm(d);
        break;
      case 'verification': {
        this.verifs.unshift(normVerification(d, ev.ts));
        this.verifs.length = Math.min(this.verifs.length, MAX_VERIFS);
        this.live.setChecks(this.verifs, true);
        break;
      }
      default: {
        if (ev.kind !== 'state' || !str(d.entity)) return;
        const ent = str(d.entity).toLowerCase().replace(/ies$/, 'y').replace(/s$/, '');
        const id = str(d.id);
        if (ent === 'sale' && this.sales.has(id)) this.sales.get(id)!.state = str(d.state);
        if (ent === 'delivery' && this.deliveries.has(id)) this.deliveries.get(id)!.state = str(d.state);
        this.opts.requestRefresh();
      }
    }
    this.touched();
  }

  private onConfirm(d: Dict): void {
    const id = str(pick(d, 'id', 'confirm_id')) || `anon-${Date.now()}`;
    const st = str(pick(d, 'state', 'status', 'answer'));
    if (st && DONE_STATES.test(st)) {
      if (this.pendingLocal.has(id)) this.pendingLocal.delete(id);
      else this.pendingBase = Math.max(0, this.pendingBase - 1);
      const ok = /committed|yes|ok|done|confirmed/i.test(st);
      this.live.addSystem(`Owner ${ok ? 'confirmed' : 'declined'}${d.prompt ? `: ${str(d.prompt)}` : ''}`, ok ? 'ok' : 'warn');
      if (this.pending() === 0) this.pendingPrompt = '';
    } else {
      if (!this.pendingLocal.has(id)) this.pendingLocal.set(id, str(d.prompt));
      this.pendingPrompt = str(d.prompt) || this.pendingPrompt;
      this.live.addSystem(`Waiting for the owner’s yes: ${str(d.prompt) || 'confirmation'}`, 'confirm');
    }
    this.opts.requestRefresh();
  }

  private pending(): number {
    return Math.max(0, this.pendingBase + this.pendingLocal.size);
  }

  private nameOf = (sku: string): string => this.products.get(sku)?.name ?? (sku || 'items');

  // ---------------------------------------------------------------- rendering

  private touched(): void {
    this.updatedAt = Date.now();
    if (this.rafId) return;
    this.rafId = requestAnimationFrame(() => {
      this.rafId = 0;
      this.renderNow();
    });
  }

  private renderNow(): void {
    for (const [hash, a] of this.links) {
      const on = hash === this.route.hash;
      a.classList.toggle('on', on);
      if (on) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    }
    this.title.textContent = this.route.label;
    document.title = `${this.route.label} · AIRA Shop Dashboard`;
    this.mockTag.hidden = !this.mock;
    this.renderConn();
    this.renderLiveBar();

    // which products changed stock since the last render (rows flash once)
    this.changed = new Set();
    for (const p of this.products.values()) {
      const prev = this.prevStock.get(p.sku);
      if (prev !== undefined && prev !== p.stock) this.changed.add(p.sku);
      this.prevStock.set(p.sku, p.stock);
    }

    if (!this.route.build) {
      this.page.className = 'page solo live-page';
      if (this.page.firstElementChild !== this.live.root) {
        this.page.replaceChildren(this.live.root);
        this.live.attached();
      }
      return;
    }
    if (!this.hasSnapshot) {
      this.page.className = 'page solo';
      this.page.replaceChildren(
        h('section', { class: 'panel' }, h('p', { class: 'blank', role: 'status', text: this.status === 'offline' ? 'Can’t reach the shop’s records. Check the connection; this page retries on its own.' : 'Loading the shop’s records…' })),
      );
      return;
    }
    const out = this.route.build(this.pageData());
    this.page.className = `page${out.solo ? ' solo' : ''}`;
    // keep scroll positions of scrolling tables across re-renders
    const scrolls = [...this.page.querySelectorAll<HTMLElement>('.grid-wrap')].map((e) => e.scrollTop);
    this.page.replaceChildren(...out.nodes);
    this.page.querySelectorAll<HTMLElement>('.grid-wrap').forEach((e, i) => {
      if (scrolls[i]) e.scrollTop = scrolls[i]!;
    });
    this.bumpTiles();
  }

  private pageData(): PageData {
    return {
      products: [...this.products.values()],
      sales: [...this.sales.values()],
      deliveries: [...this.deliveries.values()],
      verifs: this.verifs,
      pending: this.pending(),
      nameOf: this.nameOf,
      changed: this.changed,
    };
  }

  /** A tile whose value changed since the last render gets a short lift. */
  private bumpTiles(): void {
    const seen = new Map<string, string>();
    this.page.querySelectorAll<HTMLElement>('.tile[data-tile]').forEach((t) => {
      const k = t.dataset.tile!;
      const v = t.querySelector('.tile-value')?.textContent ?? '';
      seen.set(k, v);
      const prev = this.prevTiles.get(k);
      if (prev !== undefined && prev !== v) t.classList.add('bump');
    });
    for (const [k, v] of seen) this.prevTiles.set(k, v);
  }

  private renderConn(): void {
    const label: Record<ConnStatus, string> = {
      connecting: 'Connecting…',
      live: 'Live',
      polling: 'Live',
      offline: 'Offline',
      demo: 'Live · Demo',
    };
    this.conn.className = `conn ${this.status}`;
    this.conn.replaceChildren(h('span', { class: 'conn-dot', 'aria-hidden': 'true' }), h('span', { role: 'status', text: label[this.status] }));
    if (this.updatedAt !== null) this.conn.append(h('span', { class: 'conn-time', text: `Updated ${hhmm(this.updatedAt)}` }));
  }

  private renderLiveBar(): void {
    this.live.setBar(this.pending(), this.pendingPrompt, this.status);
  }
}
