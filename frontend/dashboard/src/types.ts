// Normalised shapes used by the dashboard. Raw backend payloads are coerced into
// these in util.ts so the UI never has to worry about missing fields.

export interface Product {
  sku: string;
  name: string;
  price: number;
  stock: number;
  reorder_threshold: number;
  unit: string;
}

export interface SaleItem {
  sku: string;
  name: string;
  qty: number;
  price: number | null;
}

export interface Sale {
  id: string;
  items: SaleItem[];
  total: number;
  state: string;
  method: string;
  ts: number | null;
}

export interface Delivery {
  id: string;
  sku: string;
  expected: number | null;
  counted: number | null;
  state: string;
  discrepancy: number | null;
  ts: number | null;
}

export interface Box {
  left: number;
  top: number;
  right: number;
  bottom: number;
  score: number | null;
}

export interface CountInfo {
  sku: string;
  counted: number;
  expected: number | null;
  boxes: Box[];
  frame_id: string | null;
  w: number | null;
  h: number | null;
  ts: number | null;
}

export interface Snapshot {
  products: Product[];
  sales: Sale[];
  deliveries: Delivery[];
  last_count: CountInfo | null;
  pending_confirms: number;
  mock: boolean;
}

export interface AiraEvent {
  seq: number;
  kind: string;
  data: Record<string, unknown>;
  ts: number | null;
}

export interface Reader {
  name: string;
  value: string;
  detail: string;
}

export interface Verification {
  key: string;
  type: string;
  agreed: boolean;
  total: number | null;
  readers: Reader[];
  note: string;
  ts: number;
}

export type ConnStatus = 'connecting' | 'live' | 'polling' | 'offline' | 'demo';
