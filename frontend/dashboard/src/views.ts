// Pure derivations from the dashboard state: what each page shows. No DOM here.
import type { Delivery, Product, Sale } from './types';
import { isToday } from './util';

export const VOID_SALE = /cancel|reject|void|abandon|expired/i;
export const PAID_SALE = /paid|committed|done|complete/i;

export interface ShopState {
  products: Product[];
  sales: Sale[];
  deliveries: Delivery[];
}

/* ------------------------------------------------------------------ stock */

export type StockLevel = 'out' | 'low' | 'ok';

export interface StockRow {
  product: Product;
  level: StockLevel;
}

export function stockLevel(p: Product): StockLevel {
  if (p.stock <= 0) return 'out';
  return p.stock <= p.reorder_threshold ? 'low' : 'ok';
}

/** Low stock first (emptiest first), then by name. */
export function stockRows(products: Product[]): StockRow[] {
  const rank: Record<StockLevel, number> = { out: 0, low: 1, ok: 2 };
  return products
    .map((product) => ({ product, level: stockLevel(product) }))
    .sort(
      (a, b) =>
        rank[a.level] - rank[b.level] ||
        (a.level === 'ok' ? 0 : a.product.stock - b.product.stock) ||
        a.product.name.localeCompare(b.product.name),
    );
}

export function qtyText(n: number, unit: string): string {
  const u = unit || 'piece';
  return `${n} ${n === 1 ? u : plural(u)}`;
}

function plural(u: string): string {
  if (/(s|x|ch|sh)$/i.test(u)) return `${u}es`;
  return `${u}s`;
}

/* ------------------------------------------------------------------ prices */

const GROUPS: Array<[RegExp, string]> = [
  [/grip|stump/i, 'Accessories'],
  [/ball/i, 'Balls'],
  [/willow|\bbat\b/i, 'Bats'],
  [/glove|pad|helmet|guard/i, 'Protective gear'],
];
const GROUP_ORDER = ['Balls', 'Bats', 'Protective gear', 'Accessories'];

/** A shelf group from the product name; anything unmatched is "Accessories". */
export function priceGroup(p: Product): string {
  for (const [re, g] of GROUPS) if (re.test(p.name)) return g;
  return 'Accessories';
}

export interface PriceGroup {
  name: string;
  products: Product[];
}

export function priceGroups(products: Product[]): PriceGroup[] {
    const map = new Map<string, Product[]>();
  for (const p of products) {
    const g = priceGroup(p);
    map.set(g, [...(map.get(g) ?? []), p]);
  }
  return GROUP_ORDER
    .filter((g) => map.has(g))
    .map((name) => ({ name, products: map.get(name)!.sort((a, b) => a.price - b.price || a.name.localeCompare(b.name)) }));
}

/* ------------------------------------------------------------------ today */

export interface TodaySummary {
  sales: Sale[];
  revenue: number;
  count: number;
  average: number | null;
  topProduct: string | null;
  cash: number;
  upi: number;
  other: number;
}

export function todaySummary(all: Sale[], nameOf: (sku: string) => string): TodaySummary {
  const sales = all.filter((s) => isToday(s.ts) && !VOID_SALE.test(s.state)).sort((a, b) => (b.ts ?? 0) - (a.ts ?? 0));
  const revenue = sales.reduce((n, s) => n + s.total, 0);
  const qty = new Map<string, number>();
  for (const s of sales) {
    for (const i of s.items) {
      const k = i.name || nameOf(i.sku);
      qty.set(k, (qty.get(k) ?? 0) + i.qty);
    }
  }
  let topProduct: string | null = null;
  let best = 0;
  for (const [k, n] of qty) {
    if (n > best) {
      best = n;
      topProduct = k;
    }
  }
  let cash = 0;
  let upi = 0;
  let other = 0;
  for (const s of sales) {
    if (!PAID_SALE.test(s.state)) continue;
    const m = s.method.toLowerCase();
    if (m === 'cash') cash += s.total;
    else if (m === 'upi' || m === 'card' || m === 'online') upi += s.total;
    else other += s.total;
  }
  return {
    sales,
    revenue,
    count: sales.length,
    average: sales.length ? revenue / sales.length : null,
    topProduct,
    cash,
    upi,
    other,
  };
}

/* ------------------------------------------------------------------ deliveries */

export type Tone = 'arrived' | 'waiting' | 'checked' | 'done' | 'closed' | 'problem';

export interface DeliveryView {
  delivery: Delivery;
  diff: number | null;
  tone: Tone;
  label: string;
  /** Index of the current step (0..3); steps before it are done. 4 = all done. */
  at: number;
  problem: boolean;
}

export const DELIVERY_STEPS = ['Delivery arrived', 'Counted by SAM 3.1', 'Owner said yes', 'Stock updated'];

export function deliveryView(d: Delivery): DeliveryView {
  const diff = d.discrepancy ?? (d.counted !== null && d.expected !== null ? d.counted - d.expected : null);
  const problem = diff !== null && diff !== 0;
  const st = d.state.toLowerCase();
  if (/reject|cancel|declin|void/.test(st)) return { delivery: d, diff, tone: 'closed', label: 'Declined', at: 2, problem };
  if (/commit|done|stocked|closed|confirmed/.test(st)) {
    return { delivery: d, diff, tone: problem ? 'problem' : 'done', label: problem ? 'Stocked, short' : 'Stock updated', at: 4, problem };
  }
  if (d.counted !== null || /count|check/.test(st)) {
    return { delivery: d, diff, tone: problem ? 'problem' : 'waiting', label: problem ? 'Discrepancy' : 'Waiting for a yes', at: 2, problem };
  }
  return { delivery: d, diff, tone: 'arrived', label: 'Arrived', at: 1, problem };
}

export function deliveryViews(list: Delivery[]): DeliveryView[] {
  return list
    .slice()
    .sort((a, b) => (b.ts ?? 0) - (a.ts ?? 0))
    .map(deliveryView)
    .sort((a, b) => Number(b.problem && b.at < 4) - Number(a.problem && a.at < 4));
}
