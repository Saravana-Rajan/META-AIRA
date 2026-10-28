// Page builders: Overview, Today, Stock, Prices, Orders & deliveries. Each returns fresh nodes for the
// page container; the Live page lives in live.ts because its parts are long-lived (feed, camera canvas).
import { icon, type IconName } from './icons';
import type { Product, Sale, Verification } from './types';
import { h, rupees } from './util';
import {
  DELIVERY_STEPS,
  PAID_SALE,
  deliveryViews,
  priceGroups,
  qtyText,
  stockRows,
  todaySummary,
  type DeliveryView,
  type StockRow,
  type TodaySummary,
  type Tone,
} from './views';
import type { Delivery } from './types';

export interface PageData {
  products: Product[];
  sales: Sale[];
  deliveries: Delivery[];
  verifs: Verification[];
  pending: number;
  nameOf(sku: string): string;
  /** Products whose stock changed since the last render (row flash). */
  changed: Set<string>;
}

export interface PageOut {
  solo: boolean;
  nodes: Node[];
}

/* ------------------------------------------------------------------ formatting */

export function hhmm(ts: number | null): string {
  if (ts === null) return '—';
  return new Date(ts).toLocaleTimeString('en-IN', { hour: 'numeric', minute: '2-digit', hour12: true });
}

export function dayTime(ts: number | null): string {
  if (ts === null) return '—';
  const d = new Date(ts);
  return `${d.getDate()} ${d.toLocaleString('en-IN', { month: 'short' })}, ${hhmm(ts)}`;
}

function pct(part: number, total: number): string {
  return total > 0 ? `${Math.round((part / total) * 100)}%` : '0%';
}

/* ------------------------------------------------------------------ shared pieces */

function tile(label: string, value: string, note?: string, tone?: 'warn' | 'bad' | 'good', ic?: IconName): HTMLElement {
  return h(
    'div',
    { class: `tile${tone ? ` ${tone}` : ''}`, 'data-tile': label },
    ic ? h('span', { class: 'tile-icon' }, icon(ic, 18)) : null,
    h('span', { class: 'tile-label', text: label }),
    h('span', { class: 'tile-value', text: value }),
    note ? h('span', { class: 'tile-note', text: note }) : null,
  );
}

function table(caption: string, head: Array<[string, string?]>, rows: Node[], cls = ''): HTMLElement {
  return h(
    'div',
    { class: 'grid-wrap', tabindex: 0, role: 'region', 'aria-label': caption },
    h(
      'table',
      { class: `grid ${cls}` },
      h('caption', { text: caption }),
      h('thead', null, h('tr', null, ...head.map(([t, c]) => h('th', { scope: 'col', class: c ?? null, text: t })))),
      h('tbody', null, ...rows),
    ),
  );
}

function emptyRow(cols: number, text: string): HTMLElement {
  return h('tr', { class: 'empty-row' }, h('td', { colspan: cols, text }));
}

function panel(title: string | null, cls: string, ...children: Array<Node | null>): HTMLElement {
  return h('section', { class: `panel ${cls}`.trim() }, title ? h('h2', { class: 'panel-title', text: title }) : null, ...children);
}

/* ------------------------------------------------------------------ stock */

function levelTag(r: StockRow): HTMLElement {
  if (r.level === 'ok') return h('span', { class: 'tag ok', text: 'OK' });
  return h('span', { class: 'tag low', text: r.level === 'out' ? 'Out of stock' : 'Low stock' });
}

function stockTable(d: PageData): HTMLElement {
  const rows = stockRows(d.products).map((r) => {
    const p = r.product;
    const cls = [r.level !== 'ok' ? 'low' : '', d.changed.has(p.sku) ? 'flash' : ''].filter(Boolean).join(' ');
    return h(
      'tr',
      { class: cls || null },
      h('th', { scope: 'row' }, h('span', { text: p.name }), h('span', { class: 'narrow', text: `${p.sku} · reorder at ${p.reorder_threshold}` })),
      h('td', { class: 'r', text: qtyText(p.stock, p.unit) }),
      h('td', null, levelTag(r)),
      h('td', { class: 'r wide muted', text: qtyText(p.reorder_threshold, p.unit) }),
      h('td', { class: 'wide muted', text: p.sku }),
    );
  });
  return table(
    'Stock on hand, low stock first',
    [['Product'], ['On hand', 'r'], ['Status'], ['Reorder at', 'r wide'], ['SKU', 'wide']],
    rows.length ? rows : [emptyRow(5, 'Loading the catalog…')],
  );
}

export function stockPage(d: PageData): PageOut {
  return { solo: true, nodes: [panel(null, '', stockTable(d))] };
}

/* ------------------------------------------------------------------ prices */

export function pricesPage(d: PageData): PageOut {
  const rows: Node[] = [];
  for (const g of priceGroups(d.products)) {
    rows.push(h('tr', { class: 'group' }, h('th', { colspan: 4, scope: 'colgroup', text: g.name })));
    for (const p of g.products) {
      rows.push(
        h(
          'tr',
          null,
          h('th', { scope: 'row' }, h('span', { text: p.name }), h('span', { class: 'narrow', text: `per ${p.unit}` })),
          h('td', { class: 'wide muted', text: p.sku }),
          h('td', { class: 'wide', text: p.unit }),
          h('td', { class: 'r price', text: rupees(p.price) }),
        ),
      );
    }
  }
  return {
    solo: true,
    nodes: [
      panel(
        null,
        '',
        h('p', { class: 'panel-lead', text: 'Today’s selling prices, for the shop owner. AIRA quotes these when it bills a sale by voice.' }),
        table('Price list by shelf, cheapest first', [['Product'], ['SKU', 'wide'], ['Unit', 'wide'], ['Price', 'r']], rows.length ? rows : [emptyRow(4, 'Loading the catalog…')], 'price-grid'),
      ),
    ],
  };
}

/* ------------------------------------------------------------------ today */

function todayTiles(t: TodaySummary): HTMLElement {
  return h(
    'div',
    { class: 'today-tiles' },
    tile('Revenue', rupees(t.revenue), undefined, undefined, 'rupee'),
    tile('Sales', String(t.count), undefined, undefined, 'cart'),
    tile('Average sale', t.average === null ? '—' : rupees(t.average), undefined, undefined, 'trend'),
    tile('Top product', t.topProduct ?? 'None yet', undefined, undefined, 'star'),
  );
}

function paymentsBox(t: TodaySummary): HTMLElement {
  const paid = t.cash + t.upi + t.other;
  const bar = paid
    ? h(
        'div',
        { class: 'paybar', role: 'img', 'aria-label': `Cash ${pct(t.cash, paid)}, UPI ${pct(t.upi, paid)}` },
        h('span', { class: 'cash', style: `width:${(t.cash / paid) * 100}%` }),
        h('span', { class: 'upi', style: `width:${(t.upi / paid) * 100}%` }),
      )
    : h('div', { class: 'paybar none', 'aria-hidden': 'true' });
  return h(
    'section',
    { class: 'box' },
    h('h3', { class: 'box-title', text: 'Payments' }),
    bar,
    paid ? null : h('p', { class: 'blank', text: 'No payments yet' }),
    h(
      'dl',
      { class: 'paylist' },
      h('div', { class: 'payrow' }, h('dt', { text: 'Cash received' }), h('dd', { text: rupees(t.cash) }), h('dd', { class: 'pct', text: pct(t.cash, paid) })),
      h('div', { class: 'payrow upi' }, h('dt', { text: 'UPI received' }), h('dd', { text: rupees(t.upi) }), h('dd', { class: 'pct', text: pct(t.upi, paid) })),
    ),
  );
}

function payTag(s: Sale): HTMLElement {
  if (!PAID_SALE.test(s.state)) return h('span', { class: 'tag wait', text: 'Pending' });
  const m = s.method.toLowerCase();
  if (m === 'cash') return h('span', { class: 'tag cash', text: 'Cash' });
  return h('span', { class: 'tag upi', text: m ? m.toUpperCase() : 'Paid' });
}

function salesBox(t: TodaySummary, d: PageData, limit: number): HTMLElement {
  const rows = t.sales.slice(0, limit).map((s) => {
    const items = s.items.map((i) => `${i.qty} × ${i.name || d.nameOf(i.sku)}`).join(', ') || '—';
    return h(
      'tr',
      null,
      h('td', { class: 't' }, hhmm(s.ts), h('span', { class: 'narrow items-sub', text: items })),
      h('td', { class: 'items wide', title: items, text: items }),
      h('td', { class: 'r', text: rupees(s.total) }),
      h('td', null, payTag(s)),
    );
  });
  return h(
    'section',
    { class: 'box' },
    h('h3', { class: 'box-title', text: 'Today’s sales' }),
    table(`Newest first, up to ${limit} sales`, [['Time'], ['Items', 'wide'], ['Amount', 'r'], ['Payment']], rows.length ? rows : [emptyRow(4, 'No sales yet today — sales made through AIRA appear here.')]),
  );
}

function todayBlock(d: PageData, limit = 20): HTMLElement[] {
  const t = todaySummary(d.sales, d.nameOf);
  return [todayTiles(t), h('div', { class: 'today-split' }, paymentsBox(t), salesBox(t, d, limit))];
}

export function todayPage(d: PageData): PageOut {
  return { solo: true, nodes: [h('div', { class: 'today-wrap' }, ...todayBlock(d))] };
}

/* ------------------------------------------------------------------ deliveries */

const TONE_CLASS: Record<Tone, string> = {
  arrived: 't-arrived',
  waiting: 't-waiting',
  checked: 't-checked',
  done: 't-done',
  closed: 't-closed',
  problem: 't-problem',
};

function deliveryCard(v: DeliveryView, nameOf: (sku: string) => string): HTMLElement {
  const d = v.delivery;
  const name = nameOf(d.sku);
  const steps = DELIVERY_STEPS.map((label, i) => {
    let cls = '';
    let text = label;
    if (i === 1 && v.problem && v.at >= 2) {
      cls = 'problem';
      text = 'Count mismatch';
    } else if (i < v.at) cls = 'done';
    else if (i === v.at) cls = 'now';
    return h('li', { class: `step ${cls}`.trim() }, h('span', { class: 'dot', 'aria-hidden': 'true' }), h('span', { text }));
  });
  const shortBy = v.diff !== null && v.diff < 0 ? -v.diff : 0;
  const extra = v.diff !== null && v.diff > 0 ? v.diff : 0;
  return h(
    'article',
    { class: `dcard ${TONE_CLASS[v.tone]}` },
    h(
      'div',
      { class: 'dcard-head' },
      h(
        'div',
        null,
        h('h3', { class: 'dcard-title', text: `Delivery ${d.id}` }),
        h('p', { class: 'dcard-sub', text: `${name}: ${d.expected ?? '?'} on the invoice${d.counted !== null ? `, ${d.counted} counted` : ''}` }),
      ),
      h('p', { class: 'dcard-state' }, h('span', { class: 'state-pill', text: v.label }), h('span', { class: 'dcard-time', text: `Updated ${dayTime(d.ts)}` })),
    ),
    h('ol', { class: 'steps', style: `--n:${DELIVERY_STEPS.length}`, 'aria-label': 'Delivery progress' }, ...steps),
    v.problem
      ? h(
          'div',
          { class: 'alert', role: 'note' },
          h('h4', { text: 'The delivery does not match the invoice' }),
          h(
            'ul',
            null,
            h('li', { text: `${name}: counted ${d.counted ?? '?'} of ${d.expected ?? '?'} on the invoice` }),
            shortBy ? h('li', { text: `${shortBy} short — noted for the supplier` }) : null,
            extra ? h('li', { text: `${extra} more than billed` }) : null,
          ),
        )
      : null,
    d.counted !== null
      ? h(
          'div',
          { class: 'chips' },
          h('span', { class: 'chip sam' }, icon('scan', 16), h('b', { text: 'SAM 3.1:' }), h('span', { text: `${d.counted} counted on camera` })),
          h('span', { class: 'chip' }, icon('doc', 16), h('b', { text: 'Invoice:' }), h('span', { text: `${d.expected ?? '?'} billed` })),
        )
      : null,
  );
}

function statusCounts(views: DeliveryView[]): HTMLElement {
  const order: Array<[Tone, string]> = [
    ['problem', 'Discrepancy'],
    ['arrived', 'Arrived'],
    ['waiting', 'Waiting for a yes'],
    ['done', 'Stock updated'],
    ['closed', 'Declined'],
  ];
  return h(
    'ul',
    { class: 'counts', 'aria-label': 'Deliveries by status' },
    ...order
      .map(([tone, label]) => [tone, label, views.filter((v) => v.tone === tone).length] as const)
      .filter(([, , n]) => n > 0)
      .map(([tone, label, n]) => h('li', { class: `state-pill ${TONE_CLASS[tone]}` }, `${label} `, h('b', { text: String(n) }))),
  );
}

function supplierOrdersEmpty(): HTMLElement {
  return h(
    'div',
    null,
    h('div', { class: 'section-head' }, h('h2', { text: 'Supplier orders' }), h('span', { text: 'Not connected yet' })),
    h(
      'p',
      { class: 'blank' },
      'No supplier orders to show. This build of AIRA checks deliveries as they arrive; placing and tracking orders with suppliers is not wired in yet.',
    ),
  );
}

export function ordersPage(d: PageData): PageOut {
  const views = deliveryViews(d.deliveries);
  const body: Node[] = [];
  if (views.length) {
    body.push(statusCounts(views));
    body.push(h('div', { class: 'section-head' }, h('h2', { text: 'Deliveries' }), h('span', { text: `${views.length} checked with SAM 3.1` })));
    body.push(h('div', { class: 'cards' }, ...views.map((v) => deliveryCard(v, d.nameOf))));
  } else {
    body.push(
      h(
        'div',
        { class: 'void-state' },
        h('strong', { text: 'No deliveries yet' }),
        'When a delivery arrives, the owner asks AIRA to check it: two models read the invoice and SAM 3.1 counts every item on camera. Each delivery appears here with its progress.',
      ),
    );
  }
  body.push(h('div', { style: 'margin-top:24px' }, supplierOrdersEmpty()));
  return { solo: true, nodes: [panel(null, '', ...body)] };
}

/* ------------------------------------------------------------------ verification cards (overview + live) */

export function checkCard(v: Verification, fresh: boolean): HTMLElement {
  return h(
    'article',
    { class: `check${fresh ? ' new' : ''}` },
    h(
      'div',
      { class: 'check-head' },
      h('span', { class: 'check-kind', text: v.type === 'invoice' ? 'Invoice' : v.type === 'cash' ? 'Cash' : v.type }),
      h('span', { class: 'check-total', text: v.total !== null ? rupees(v.total) : '—' }),
      h('span', { class: `verdict ${v.agreed ? 'yes' : 'no'}`, text: v.agreed ? 'Both agree' : 'Disagree' }),
      h('time', { class: 'check-time', text: hhmm(v.ts) }),
    ),
    h(
      'div',
      { class: 'readers' },
      ...v.readers.map((r) =>
        h('div', { class: 'reader' }, h('span', { class: 'reader-name', text: r.name }), h('span', { class: 'reader-val', text: r.value }), r.detail ? h('span', { class: 'reader-det', text: r.detail }) : null),
      ),
    ),
    v.note ? h('p', { class: 'check-note', text: v.note }) : null,
  );
}

/* ------------------------------------------------------------------ overview */

export function overviewPage(d: PageData): PageOut {
  const t = todaySummary(d.sales, d.nameOf);
  const rows = stockRows(d.products);
  const low = rows.filter((r) => r.level !== 'ok').length;
  const views = deliveryViews(d.deliveries);
  const short = views.filter((v) => v.problem).length;

  const tiles = h(
    'div',
    { class: 'tiles span-all' },
    tile('Revenue today', rupees(t.revenue), `${t.count} sale${t.count === 1 ? '' : 's'}`),
    tile('Low stock', String(low), `of ${d.products.length} products`, low ? 'warn' : undefined),
    tile('Deliveries checked', String(views.length), short ? `${short} with a discrepancy` : 'all counts match', short ? 'bad' : undefined),
    tile('Waiting for a yes', String(d.pending), d.pending ? 'the owner is being asked' : 'nothing waiting', d.pending ? 'warn' : undefined),
  );

  const today = panel('Today', '', ...todayBlock(d, 6));
  const stock = panel('Stock', 'span-2', stockTable(d));

  const recent = views.slice(0, 3);
  const orders = panel(
    'Orders & deliveries',
    'span-2',
    recent.length
      ? h('div', { class: 'cards' }, ...recent.map((v) => deliveryCard(v, d.nameOf)))
      : h('p', { class: 'blank', text: 'No deliveries checked yet. Supplier orders are not connected in this build.' }),
    h('a', { class: 'more', href: '#/orders', text: recent.length ? `See all ${views.length} deliveries` : 'Open orders & deliveries' }),
  );

  const agreed = d.verifs.filter((v) => v.agreed).length;
  const checks = panel(
    'Two-model checks',
    '',
    h('p', { class: 'panel-lead', text: d.verifs.length ? `${agreed} of ${d.verifs.length} readings agreed. AIRA only speaks a number when both models agree.` : 'Cash and invoice readings appear here. Two Meta models read each one on their own; AIRA only speaks a number when both agree.' }),
    h('div', { class: 'checks' }, ...d.verifs.slice(0, 3).map((v) => checkCard(v, false))),
    h('a', { class: 'more', href: '#/live', text: 'Open Live' }),
  );

  return { solo: false, nodes: [tiles, today, stock, orders, checks] };
}
