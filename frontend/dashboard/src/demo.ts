// ?demo=1 — realistic fake shop with a scripted, looping live conversation.
// Everything goes through the same applySnapshot/applyEvent path as the real backend.
import type { AiraEvent, Box, Product, Sale, Snapshot } from './types';

interface DemoSink {
  applySnapshot(s: Snapshot): void;
  applyEvent(e: AiraEvent): void;
}

const frames = new Map<string, string>();
export function demoFrameSrc(id: string): string | null {
  return frames.get(id) ?? null;
}

const products: Product[] = [
  { sku: 'BAT-SS-EW', name: 'SS English Willow Bat', price: 2450, stock: 6, reorder_threshold: 3, unit: 'piece' },
  { sku: 'BALL-LEA-RED', name: 'Leather Cricket Ball (Red)', price: 450, stock: 4, reorder_threshold: 6, unit: 'piece' },
  { sku: 'BALL-TEN-COS', name: 'Cosco Tennis Ball', price: 120, stock: 38, reorder_threshold: 12, unit: 'piece' },
  { sku: 'GRIP-BAT', name: 'Bat Grip', price: 80, stock: 21, reorder_threshold: 10, unit: 'piece' },
  { sku: 'SHUTTLE-YNX', name: 'Yonex Mavis 350 Shuttles', price: 1150, stock: 9, reorder_threshold: 4, unit: 'pack' },
  { sku: 'RKT-YNX-NR', name: 'Yonex Nanoray Racket', price: 3200, stock: 3, reorder_threshold: 2, unit: 'piece' },
  { sku: 'FB-NIV-5', name: 'Nivia Football (Size 5)', price: 899, stock: 7, reorder_threshold: 4, unit: 'piece' },
  { sku: 'SKIP-ROPE', name: 'Skipping Rope', price: 199, stock: 2, reorder_threshold: 5, unit: 'piece' },
];

function makeSnapshot(sales: Sale[]): Snapshot {
  return {
    products: products.map((p) => ({ ...p })),
    sales: sales.map((s) => ({ ...s, items: s.items.map((i) => ({ ...i })) })),
    deliveries: [],
    last_count: null,
    pending_confirms: 0,
    mock: true,
  };
}

/** Draw a fake "cardboard box of balls" photo and return SAM-style boxes in source pixels. */
function makeFrame(id: string, n: number, kind: 'ball' | 'can'): Box[] {
  const W = 1280;
  const H = 960;
  const c = document.createElement('canvas');
  c.width = W;
  c.height = H;
  const ctx = c.getContext('2d');
  const boxes: Box[] = [];
  if (!ctx) return boxes;
  // table + box
  const bg = ctx.createLinearGradient(0, 0, 0, H);
  bg.addColorStop(0, '#3b3026');
  bg.addColorStop(1, '#1e1812');
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, W, H);
  ctx.fillStyle = '#b48a57';
  ctx.fillRect(110, 120, W - 220, H - 230);
  ctx.fillStyle = '#9b7444';
  ctx.fillRect(140, 150, W - 280, H - 290);
  // noise
  for (let i = 0; i < 2500; i++) {
    ctx.fillStyle = `rgba(0,0,0,${Math.random() * 0.08})`;
    ctx.fillRect(Math.random() * W, Math.random() * H, 2, 2);
  }
  const cols = 4;
  const rows = Math.ceil(n / cols);
  const cellW = (W - 360) / cols;
  const cellH = (H - 380) / Math.max(rows, 3);
  for (let i = 0; i < n; i++) {
    const col = i % cols;
    const row = Math.floor(i / cols);
    const r = Math.min(cellW, cellH) * 0.4;
    const cx = 180 + cellW * (col + 0.5) + (Math.random() - 0.5) * 22;
    const cy = 200 + cellH * (row + 0.5) + (Math.random() - 0.5) * 18;
    if (kind === 'ball') {
      const g = ctx.createRadialGradient(cx - r * 0.35, cy - r * 0.35, r * 0.1, cx, cy, r);
      g.addColorStop(0, '#ff8a7a');
      g.addColorStop(0.5, '#b3221b');
      g.addColorStop(1, '#4d0b08');
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = 'rgba(255,240,220,0.75)';
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.arc(cx, cy, r * 0.92, -0.35 * Math.PI + 0.2, 0.35 * Math.PI + 0.2);
      ctx.stroke();
      boxes.push(box(cx - r, cy - r, cx + r, cy + r));
    } else {
      const g = ctx.createLinearGradient(cx - r, 0, cx + r, 0);
      g.addColorStop(0, '#0d3b8a');
      g.addColorStop(0.5, '#2f7ae5');
      g.addColorStop(1, '#0b2c66');
      ctx.fillStyle = g;
      ctx.fillRect(cx - r * 0.7, cy - r, r * 1.4, r * 2);
      ctx.fillStyle = '#e8f04a';
      ctx.fillRect(cx - r * 0.7, cy - r * 0.2, r * 1.4, r * 0.4);
      boxes.push(box(cx - r * 0.7, cy - r, cx + r * 0.7, cy + r));
    }
  }
  frames.set(id, c.toDataURL('image/jpeg', 0.85));
  return boxes;
}

function box(l: number, t: number, r: number, b: number): Box {
  const pad = 6;
  return {
    left: Math.round(l - pad),
    top: Math.round(t - pad),
    right: Math.round(r + pad),
    bottom: Math.round(b + pad),
    score: Math.round((0.86 + Math.random() * 0.13) * 100) / 100,
  };
}

type Step = [delayMs: number, run: () => void];

export function startDemo(sink: DemoSink): void {
  let seq = 0;
  let saleNo = 1040;
  let delNo = 7;
  let confirmNo = 1;
  let frameNo = 1;
  const now = Date.now();
  const sales: Sale[] = [
    mkSale('S-1036', [['BAT-SS-EW', 1], ['GRIP-BAT', 2]], 'paid', 'upi', now - 3.2 * 3600e3),
    mkSale('S-1037', [['BALL-TEN-COS', 6]], 'paid', 'cash', now - 2.4 * 3600e3),
    mkSale('S-1038', [['SHUTTLE-YNX', 1]], 'paid', 'upi', now - 1.5 * 3600e3),
    mkSale('S-1039', [['FB-NIV-5', 1], ['SKIP-ROPE', 1]], 'paid', 'cash', now - 0.6 * 3600e3),
  ];

  function mkSale(id: string, lines: [string, number][], state: string, method: string, ts: number): Sale {
    const items = lines.map(([sku, qty]) => {
      const p = products.find((x) => x.sku === sku)!;
      return { sku, name: p.name, qty, price: p.price };
    });
    return { id, items, total: items.reduce((s, i) => s + (i.price ?? 0) * i.qty, 0), state, method, ts };
  }

  const emit = (kind: string, data: Record<string, unknown>) =>
    sink.applyEvent({ seq: ++seq, kind, data, ts: Date.now() });
  const say = (role: 'user' | 'aira', text: string) => {
    // stream user speech as partials first, like Voice Transcribe does
    if (role === 'user') {
      const words = text.split(' ');
      const cut = Math.max(1, Math.floor(words.length / 2));
      emit('transcript', { role, text: words.slice(0, cut).join(' ') + '…', final: false });
      window.setTimeout(() => emit('transcript', { role, text, final: true }), 650);
    } else emit('transcript', { role, text, final: true });
  };
  const stock = (sku: string, delta: number) => {
    const p = products.find((x) => x.sku === sku);
    if (!p) return;
    p.stock = Math.max(0, p.stock + delta);
    emit('ledger', { sku, delta, reason: delta < 0 ? 'SALE' : 'DELIVERY', on_hand: p.stock });
  };
  const push = () => sink.applySnapshot(makeSnapshot(sales));

  sink.applySnapshot(makeSnapshot(sales));

  const scenarios: Array<() => Step[]> = [
    // 1) cash sale with two-model note reading
    () => {
      const id = `S-${++saleNo}`;
      const cid = `c${confirmNo++}`;
      const sale = mkSale(id, [['BALL-TEN-COS', 2], ['GRIP-BAT', 1]], 'open', 'cash', Date.now());
      return [
        [1200, () => say('user', 'Two Cosco tennis balls and one bat grip')],
        [2200, () => say('aira', 'Two Cosco tennis balls at ₹120 each and one bat grip at ₹80. Total ₹320. Shall I bill it?')],
        [600, () => { sales.push(sale); emit('sale', { ...sale }); }],
        [600, () => emit('confirm', { id: cid, kind: 'sale', prompt: 'Bill ₹320 — 2 tennis balls, 1 grip?' })],
        [2400, () => say('user', 'Yes')],
        [700, () => emit('confirm', { id: cid, state: 'committed', prompt: 'Bill ₹320' })],
        [300, () => { stock('BALL-TEN-COS', -2); stock('GRIP-BAT', -1); push(); }],
        [1400, () => say('aira', 'Billed. Customer is paying cash — hold the notes up to the camera.')],
        [2600, () => emit('verification', { type: 'cash', agreed: true, total: 500, readers: [
          { model: 'Muse Spark', total: 500, notes: { 200: 2, 100: 1 } },
          { model: 'Glimmer', total: 500, notes: { 200: 2, 100: 1 } },
        ] })],
        [500, () => { sale.state = 'paid'; emit('payment', { sale_id: id, method: 'cash', amount: 500, change: 180 }); push(); }],
        [900, () => say('aira', 'Both readers see ₹500: two 200s and one 100. Give back ₹180 change.')],
      ];
    },
    // 2) delivery with SAM count shortfall + invoice reading
    () => {
      const fid = `demo-frame-${frameNo++}`;
      const did = `D-${String(++delNo).padStart(3, '0')}`;
      const cid = `c${confirmNo++}`;
      const counted = 10;
      const expected = 12;
      const boxes = makeFrame(fid, counted, 'ball');
      return [
        [1500, () => say('user', 'The leather ball delivery came. Check the invoice.')],
        [2000, () => say('aira', 'Hold the invoice flat in front of the camera.')],
        [2600, () => emit('verification', { type: 'invoice', agreed: true, total: 5400, readers: [
          { model: 'Muse Spark', total: 5400, supplier: 'Jalandhar Sports Co.' },
          { model: 'Glimmer', total: 5400, supplier: 'Jalandhar Sports Co.' },
        ] })],
        [900, () => say('aira', 'Invoice from Jalandhar Sports: 12 leather balls, ₹5,400. Now show me the open box.')],
        [2600, () => emit('count', { sku: 'BALL-LEA-RED', counted, expected, boxes, frame_id: fid, w: 1280, h: 960 })],
        [400, () => emit('delivery', { id: did, sku: 'BALL-LEA-RED', expected, counted, state: 'counted', discrepancy: counted - expected, ts: Date.now() })],
        [1300, () => say('aira', 'I counted 10 leather balls. The invoice says 12 — 2 are missing. Add 10 to stock?')],
        [300, () => emit('confirm', { id: cid, kind: 'delivery', prompt: 'Add 10 leather balls to stock (2 short)?' })],
        [2600, () => say('user', 'Yes, add ten')],
        [600, () => emit('confirm', { id: cid, state: 'committed', prompt: 'Add 10 leather balls' })],
        [300, () => { stock('BALL-LEA-RED', counted); emit('delivery', { id: did, sku: 'BALL-LEA-RED', expected, counted, state: 'committed', discrepancy: -2, ts: Date.now() }); push(); }],
        [1200, () => say('aira', 'Done. Stock is now ' + products.find((p) => p.sku === 'BALL-LEA-RED')!.stock + '. I noted 2 short for the supplier.')],
      ];
    },
    // 3) UPI sale, then cash with model disagreement
    () => {
      const id = `S-${++saleNo}`;
      const cid = `c${confirmNo++}`;
      const sale = mkSale(id, [['SHUTTLE-YNX', 1], ['RKT-YNX-NR', 1]], 'open', 'cash', Date.now());
      return [
        [1500, () => say('user', 'How many Nanoray rackets do we have?')],
        [1800, () => say('aira', `${products.find((p) => p.sku === 'RKT-YNX-NR')!.stock} Yonex Nanoray rackets in stock, ₹3,200 each.`)],
        [2400, () => say('user', 'Sell one racket and one box of Mavis shuttles')],
        [1800, () => say('aira', 'One Nanoray racket and one Mavis 350 tube. Total ₹4,350. Bill it?')],
        [400, () => { sales.push(sale); emit('sale', { ...sale }); emit('confirm', { id: cid, kind: 'sale', prompt: 'Bill ₹4,350 — racket + shuttles?' }); }],
        [2200, () => say('user', 'Yes. Cash.')],
        [600, () => { emit('confirm', { id: cid, state: 'committed' }); stock('RKT-YNX-NR', -1); stock('SHUTTLE-YNX', -1); push(); }],
        [2400, () => emit('verification', { type: 'cash', agreed: false, total: null, hint: 'Readers disagree — one note is folded', readers: [
          { model: 'Muse Spark', total: 4500, notes: { 500: 9 } },
          { model: 'Glimmer', total: 4100, notes: { 500: 8, 100: 1 } },
        ] })],
        [900, () => say('aira', 'I am not sure of the amount. Please unfold the notes and show them again.')],
        [2600, () => emit('verification', { type: 'cash', agreed: true, total: 4500, readers: [
          { model: 'Muse Spark', total: 4500, notes: { 500: 9 } },
          { model: 'Glimmer', total: 4500, notes: { 500: 9 } },
        ] })],
        [500, () => { sale.state = 'paid'; emit('payment', { sale_id: id, method: 'cash', amount: 4500, change: 150 }); push(); }],
        [800, () => say('aira', 'Both readers agree: ₹4,500 in 500s. Give back ₹150.')],
      ];
    },
  ];

  let si = 0;
  const runScenario = () => {
    const steps = scenarios[si % scenarios.length]!();
    si++;
    let t = 0;
    for (const [delay, fn] of steps) {
      t += delay;
      window.setTimeout(fn, t);
    }
    window.setTimeout(runScenario, t + 3500);
  };
  // Seed a first count so the SAM panel is never empty in screenshots.
  const fid0 = `demo-frame-${frameNo++}`;
  const b0 = makeFrame(fid0, 12, 'can');
  emit('count', { sku: 'BALL-TEN-COS', counted: 12, expected: 12, boxes: b0, frame_id: fid0, w: 1280, h: 960, ts: now - 40 * 60e3 });
  emit('verification', { type: 'invoice', agreed: true, total: 1440, readers: [
    { model: 'Muse Spark', total: 1440, supplier: 'Cosco India' },
    { model: 'Glimmer', total: 1440, supplier: 'Cosco India' },
  ] });
  window.setTimeout(runScenario, 800);
}
