import type {
  AiraEvent,
  Box,
  CountInfo,
  Delivery,
  Product,
  Reader,
  Sale,
  SaleItem,
  Snapshot,
  Verification,
} from './types';

export type Dict = Record<string, unknown>;

export function obj(v: unknown): Dict {
  return v !== null && typeof v === 'object' && !Array.isArray(v) ? (v as Dict) : {};
}

export function arr(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

export function num(v: unknown, fallback: number): number;
export function num(v: unknown, fallback: null): number | null;
export function num(v: unknown, fallback: number | null): number | null {
  if (typeof v === 'number' && Number.isFinite(v)) return v;
  if (typeof v === 'string' && v.trim() !== '') {
    const n = Number(v);
    if (Number.isFinite(n)) return n;
  }
  return fallback;
}

export function str(v: unknown, fallback = ''): string {
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  return fallback;
}

/** First defined value among keys. */
export function pick(o: Dict, ...keys: string[]): unknown {
  for (const k of keys) {
    if (o[k] !== undefined && o[k] !== null) return o[k];
  }
  return undefined;
}

/** Accepts epoch seconds, epoch ms, numeric strings, ISO strings, Firestore-ish {seconds}. */
export function normTs(v: unknown): number | null {
  if (v === null || v === undefined) return null;
  if (typeof v === 'object') {
    const o = obj(v);
    const s = num(pick(o, 'seconds', '_seconds'), null);
    return s === null ? null : s * 1000;
  }
  const n = num(v, null);
  if (n !== null) return n < 1e12 ? n * 1000 : n;
  if (typeof v === 'string') {
    const p = Date.parse(v);
    return Number.isNaN(p) ? null : p;
  }
  return null;
}

export function normKind(k: unknown): string {
  let s = str(k).toLowerCase().trim();
  if (s.endsWith('ies')) s = s.slice(0, -3) + 'y';
  else if (s.endsWith('s') && !s.endsWith('ss')) s = s.slice(0, -1);
  return s;
}

export function normProduct(v: unknown): Product | null {
  const o = obj(v);
  const sku = str(pick(o, 'sku', 'id'));
  if (!sku) return null;
  return {
    sku,
    name: str(pick(o, 'name', 'title'), sku),
    price: num(pick(o, 'price', 'mrp'), 0),
    stock: num(pick(o, 'stock', 'on_hand', 'qty', 'quantity'), 0),
    reorder_threshold: num(pick(o, 'reorder_threshold', 'reorder', 'low_stock'), 0),
    unit: str(o.unit, 'piece'),
  };
}

export function normItem(v: unknown): SaleItem {
  const o = obj(v);
  return {
    sku: str(pick(o, 'sku', 'product')),
    name: str(pick(o, 'name', 'product_name', 'title')),
    qty: num(pick(o, 'qty', 'quantity', 'count'), 1),
    price: num(pick(o, 'price', 'unit_price'), null),
  };
}

export function normSale(v: unknown): Sale | null {
  const o = obj(v);
  const id = str(pick(o, 'id', 'sale_id'));
  if (!id) return null;
  const items = arr(o.items).map(normItem);
  let total = num(pick(o, 'total', 'amount'), null);
  if (total === null) total = items.reduce((s, i) => s + (i.price ?? 0) * i.qty, 0);
  return {
    id,
    items,
    total,
    state: str(pick(o, 'state', 'status'), 'open'),
    method: str(pick(o, 'method', 'payment_method')),
    ts: normTs(pick(o, 'ts', 'created_at', 'updated_at', 'time')),
  };
}

export function normDelivery(v: unknown): Delivery | null {
  const o = obj(v);
  const id = str(pick(o, 'id', 'delivery_id'));
  if (!id) return null;
  return {
    id,
    sku: str(o.sku),
    expected: num(o.expected, null),
    counted: num(o.counted, null),
    state: str(pick(o, 'state', 'status'), 'open'),
    discrepancy: num(o.discrepancy, null),
    ts: normTs(pick(o, 'ts', 'created_at')),
  };
}

export function normBox(v: unknown): Box | null {
  if (Array.isArray(v) && v.length >= 4) {
    const [l, t, r, b] = v.map((x) => num(x, 0));
    return { left: l ?? 0, top: t ?? 0, right: r ?? 0, bottom: b ?? 0, score: num(v[4], null) };
  }
  const o = obj(v);
  const left = num(pick(o, 'left', 'x1', 'x'), null);
  const top = num(pick(o, 'top', 'y1', 'y'), null);
  if (left === null || top === null) return null;
  let right = num(pick(o, 'right', 'x2'), null);
  let bottom = num(pick(o, 'bottom', 'y2'), null);
  if (right === null) right = left + num(pick(o, 'w', 'width'), 0);
  if (bottom === null) bottom = top + num(pick(o, 'h', 'height'), 0);
  return { left, top, right, bottom, score: num(pick(o, 'score', 'conf', 'confidence'), null) };
}

export function normCount(v: unknown): CountInfo | null {
  const o = obj(v);
  if (Object.keys(o).length === 0) return null;
  const boxes = arr(o.boxes)
    .map(normBox)
    .filter((b): b is Box => b !== null);
  return {
    sku: str(pick(o, 'sku', 'product')),
    counted: num(pick(o, 'counted', 'count'), boxes.length),
    expected: num(o.expected, null),
    boxes,
    frame_id: str(pick(o, 'frame_id', 'frame')) || null,
    w: num(pick(o, 'w', 'width', 'frame_w'), null),
    h: num(pick(o, 'h', 'height', 'frame_h'), null),
    ts: normTs(o.ts),
  };
}

export function normSnapshot(v: unknown): Snapshot {
  const o = obj(v);
  return {
    products: arr(o.products)
      .map(normProduct)
      .filter((p): p is Product => p !== null),
    sales: arr(o.sales)
      .map(normSale)
      .filter((s): s is Sale => s !== null),
    deliveries: arr(o.deliveries)
      .map(normDelivery)
      .filter((d): d is Delivery => d !== null),
    last_count: o.last_count ? normCount(o.last_count) : null,
    pending_confirms: num(pick(o, 'pending_confirms', 'pending'), 0),
    mock: o.mock === true,
  };
}

export function normEvent(v: unknown): AiraEvent | null {
  const o = obj(v);
  const kind = str(pick(o, 'kind', 'type'));
  if (!kind) return null;
  return {
    seq: num(o.seq, 0),
    kind: normKind(kind),
    data: obj(o.data),
    ts: normTs(o.ts),
  };
}

let verifSeq = 0;

function readerOf(v: unknown, i: number): Reader {
  const fallbackName = `Model ${String.fromCharCode(65 + i)}`;
  if (typeof v === 'string' || typeof v === 'number') {
    return { name: fallbackName, value: String(v), detail: '' };
  }
  const o = obj(v);
  const name = str(pick(o, 'model', 'name', 'reader', 'id'), fallbackName);
  const total = pick(o, 'total', 'amount', 'value', 'reading');
  const n = num(total, null);
  const value = n !== null ? rupees(n) : str(total, '—');
  let detail = '';
  const notes = obj(o.notes);
  const noteKeys = Object.keys(notes);
  if (noteKeys.length) {
    detail = noteKeys
      .sort((a, b) => Number(b) - Number(a))
      .map((k) => `${str(notes[k])}×₹${k}`)
      .join(' + ');
  } else if (Array.isArray(o.lines)) {
    detail = `${o.lines.length} line${o.lines.length === 1 ? '' : 's'}`;
  } else if (o.supplier) {
    detail = str(o.supplier);
  }
  return { name, value, detail };
}

export function normVerification(data: Dict, ts: number | null): Verification {
  const rawReaders = arr(pick(data, 'readers', 'models', 'readings'));
  const note = [str(data.hint), ...arr(data.disagreements).map((d) => str(d))]
    .filter(Boolean)
    .join(' · ');
  return {
    key: `v${++verifSeq}`,
    type: str(pick(data, 'type', 'doc'), 'cash').toLowerCase(),
    agreed: data.agreed === true || data.match === true,
    total: num(pick(data, 'total', 'amount'), null),
    readers: rawReaders.map(readerOf),
    note,
    ts: ts ?? Date.now(),
  };
}

const inr = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });
export function rupees(n: number): string {
  return `₹${inr.format(Math.round(n))}`;
}

export function clock(ts: number | null): string {
  if (ts === null) return '';
  return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export function isToday(ts: number | null): boolean {
  if (ts === null) return true;
  const d = new Date(ts);
  const n = new Date();
  return d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate();
}

type Attrs = Record<string, string | number | boolean | null | undefined>;
type Child = Node | string | number | null | undefined | false;

/** Tiny hyperscript helper. `class` and `text` are special; other attrs are set verbatim. */
export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs: Attrs | null = null,
  ...children: Child[]
): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') e.className = String(v);
      else if (k === 'text') e.textContent = String(v);
      else e.setAttribute(k, v === true ? '' : String(v));
    }
  }
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    e.append(c instanceof Node ? c : String(c));
  }
  return e;
}

export function $(id: string): HTMLElement {
  const e = document.getElementById(id);
  if (!e) throw new Error(`missing #${id}`);
  return e;
}

/** Restart a CSS animation class on an element. */
export function retrigger(e: Element, cls: string): void {
  e.classList.remove(cls);
  void (e as HTMLElement).offsetWidth;
  e.classList.add(cls);
}
