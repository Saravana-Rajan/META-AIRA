"""Shop tools: prices, stock, sales, payments, confirmations, day summary.

Every total, change and stock figure is computed here in Python integers (rupees); the agent only
speaks the numbers these results carry.
"""

import re
import secrets
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from aira.contracts import State
from aira.tools.base import Tool, ToolContext, err, no_results, obj, ok

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_QTY = 500
SHOP_TZ = ZoneInfo("Asia/Kolkata")
PENDING, COMPLETED = "SALE_PENDING", "SALE_COMPLETED"


def rs(n: int) -> str:
    return f"₹{n}"


def _key(ctx: ToolContext) -> str:
    """Idempotency key for this tool call (random if the caller gave no call id, so calls never collide)."""
    return ctx.idem_key() if ctx.call_id else secrets.token_hex(12)


def _plural(unit: str, n: int) -> str:
    return unit if n == 1 else unit + "s"


def _lines_text(lines: list[dict]) -> str:
    return ", ".join(f"{li['qty']} {li['name']}" for li in lines)


async def _resolve_items(ctx: ToolContext, items: list[dict] | None) -> tuple[list[dict], dict | None]:
    """[{product, qty}] -> sale lines [{sku, name, unit, qty, unit_price, line_total}] or an error result."""
    if not items:
        return [], err("no items given; say which products and how many")
    lines: list[dict] = []
    unknown: list[str] = []
    for it in items:
        name = str((it or {}).get("product") or "").strip()
        qty = (it or {}).get("qty")
        if isinstance(qty, float) and qty.is_integer():
            qty = int(qty)
        if not isinstance(qty, int) or isinstance(qty, bool) or not 1 <= qty <= MAX_QTY:
            return [], err(f"quantity for {name or 'item'} must be a whole number from 1 to {MAX_QTY}")
        p = await ctx.store.find_product(name) if name else None
        if p is None:
            unknown.append(name or "?")
            continue
        lines.append(
            {
                "sku": p["sku"],
                "name": p["name"],
                "unit": p.get("unit", "piece"),
                "qty": qty,
                "unit_price": int(p["price"]),
                "line_total": int(p["price"]) * qty,
            }
        )
    if unknown:
        return [], no_results(
            f"I could not find {', '.join(unknown)} in the shop's products.", unknown=unknown
        )
    return lines, None


def _merge(lines: list[dict], extra: list[dict]) -> list[dict]:
    out = [dict(li) for li in lines]
    for e in extra:
        same = next((li for li in out if li["sku"] == e["sku"]), None)
        if same:
            same["qty"] += e["qty"]
            same["line_total"] = same["qty"] * same["unit_price"]
        else:
            out.append(dict(e))
    return out


async def _stock_problem(ctx: ToolContext, lines: list[dict]) -> dict | None:
    for li in lines:
        have = await ctx.store.stock(li["sku"])
        if li["qty"] > have:
            return err(
                f"only {have} {li['name']} in stock, cannot sell {li['qty']}",
                sku=li["sku"],
                in_stock=have,
                requested=li["qty"],
                say_en=f"Only {have} {li['name']} in stock.",
            )
    return None


def _sale_facts(sale: dict) -> dict:
    return {
        "sale_id": sale["id"],
        "items": [
            {k: li[k] for k in ("sku", "name", "qty", "unit_price", "line_total")} for li in sale["items"]
        ],
        "total": sale["total"],
        "state": sale["state"],
    }


async def _save_sale(ctx: ToolContext, sale: dict) -> None:
    sale["total"] = sum(li["line_total"] for li in sale["items"])
    sale["updated"] = time.time()
    await ctx.store.put("sales", sale["id"], sale)
    await ctx.store.add_event("sale", _sale_facts(sale))
    await ctx.send(
        State(entity="sale", id=sale["id"], state=sale["state"], summary=f"{_lines_text(sale['items'])}")
    )


async def _load_pending_sale(ctx: ToolContext, sale_id: str) -> tuple[dict | None, dict | None]:
    if not isinstance(sale_id, str) or not ID_RE.match(sale_id):
        return None, err("invalid sale_id")
    sale = await ctx.store.get("sales", sale_id)
    if sale is None:
        return None, err(f"no sale {sale_id}; start a new sale")
    return sale, None


# ---- tools ---------------------------------------------------------------------------------------
async def price_query(ctx: ToolContext, product: str | None = None) -> dict:
    if product:
        p = await ctx.store.find_product(product)
        if p is None:
            return no_results(f"I could not find {product} in the shop's products.", query=product)
        unit = p.get("unit", "piece")
        return ok(
            f"{p['name']} is {rs(p['price'])} per {unit}.",
            sku=p["sku"],
            name=p["name"],
            brand=p.get("brand"),
            unit=unit,
            price=int(p["price"]),
        )
    prods = await ctx.store.products()
    items = [
        {"sku": p["sku"], "name": p["name"], "unit": p.get("unit", "piece"), "price": int(p["price"])}
        for p in prods
    ]
    say = "; ".join(f"{i['name']} {rs(i['price'])} per {i['unit']}" for i in items)
    return ok(f"Prices: {say}.", products=items)


async def stock_query(ctx: ToolContext, product: str | None = None) -> dict:
    if product:
        p = await ctx.store.find_product(product)
        if p is None:
            return no_results(f"I could not find {product} in the shop's products.", query=product)
        n = await ctx.store.stock(p["sku"])
        thr = int(p.get("reorder_threshold", 0))
        low = n <= thr
        unit = p.get("unit", "piece")
        say = f"{n} {_plural(unit, n)} of {p['name']} in stock."
        if low:
            say += f" That is low; reorder level is {thr}."
        return ok(say, sku=p["sku"], name=p["name"], unit=unit, on_hand=n, reorder_threshold=thr, low=low)
    prods = await ctx.store.products()
    stock = await ctx.store.stock_all()
    rows = []
    for p in prods:
        n = stock.get(p["sku"], 0)
        thr = int(p.get("reorder_threshold", 0))
        rows.append(
            {
                "sku": p["sku"],
                "name": p["name"],
                "unit": p.get("unit", "piece"),
                "on_hand": n,
                "reorder_threshold": thr,
                "low": n <= thr,
            }
        )
    rows.sort(key=lambda r: (not r["low"], r["on_hand"]))
    low = [r for r in rows if r["low"]]
    parts = [f"{r['name']} {r['on_hand']}" for r in rows]
    say = (
        f"Low stock: {', '.join(f'{r["name"]} {r["on_hand"]}' for r in low)}. " if low else "Nothing is low. "
    )
    say += f"All stock: {', '.join(parts)}."
    return ok(say, stock=rows, low_count=len(low))


async def sale_start(ctx: ToolContext, items: list[dict] | None = None) -> dict:
    lines, problem = await _resolve_items(ctx, items)
    if problem:
        return problem
    lines = _merge([], lines)
    if (problem := await _stock_problem(ctx, lines)) is not None:
        return problem
    sale = {
        "id": f"S{_key(ctx)[:10]}",
        "session_id": ctx.session_id,
        "items": lines,
        "state": PENDING,
        "created": time.time(),
    }
    await _save_sale(ctx, sale)
    return ok(f"Sale started: {_lines_text(lines)}. Total {rs(sale['total'])}.", **_sale_facts(sale))


async def sale_add(ctx: ToolContext, sale_id: str = "", items: list[dict] | None = None) -> dict:
    sale, problem = await _load_pending_sale(ctx, sale_id)
    if problem:
        return problem
    if sale["state"] != PENDING:
        return err(f"sale {sale_id} is already {sale['state']}; start a new sale", state=sale["state"])
    extra, problem = await _resolve_items(ctx, items)
    if problem:
        return problem
    merged = _merge(sale["items"], extra)
    if (problem := await _stock_problem(ctx, merged)) is not None:
        return problem
    sale["items"] = merged
    await _save_sale(ctx, sale)
    return ok(
        f"Added {_lines_text(extra)}. Sale now: {_lines_text(merged)}. Total {rs(sale['total'])}.",
        added=[{"sku": e["sku"], "name": e["name"], "qty": e["qty"]} for e in extra],
        **_sale_facts(sale),
    )


async def sale_pay(ctx: ToolContext, sale_id: str = "", method: str = "cash") -> dict:
    sale, problem = await _load_pending_sale(ctx, sale_id)
    if problem:
        return problem
    if sale["state"] == COMPLETED:
        return ok(f"Sale {sale_id} is already paid and complete.", already_done=True, **_sale_facts(sale))
    if sale["state"] != PENDING:
        return err(f"sale {sale_id} is {sale['state']}", state=sale["state"])
    if not sale["items"]:
        return err("the sale has no items")
    if method not in ("cash", "upi"):
        return err("method must be cash or upi")
    total = int(sale["total"])
    facts: dict = {"sale_id": sale_id, "total": total, "method": method}
    evidence: dict
    if method == "cash":
        frame = await ctx.request_frame("cash")
        if frame is None:
            return no_results("I could not see the camera. Hold the notes in front of the camera.", **facts)
        from aira.vision.cash import read_cash  # lazy: vision module is optional at import time

        reading = await read_cash(frame)
        if not reading.agreed or reading.total is None:
            hint = reading.hint or "Spread the notes flat and show them again."
            return no_results(f"I could not read the cash clearly. {hint}", hint=hint, **facts)
        cash = int(reading.total)
        notes = {
            str(int(d)): int(c) for d, c in sorted((reading.notes or {}).items(), key=lambda kv: -int(kv[0]))
        }
        facts.update(cash_received=cash, notes=notes)
        if cash < total:
            short = total - cash
            return ok(
                f"The bill is {rs(total)} but I count {rs(cash)}. That is {rs(short)} short.",
                paid=False,
                short_by=short,
                **facts,
            )
        change = cash - total
        facts["change"] = change
        change_say = f"Give back {rs(change)} change." if change else "Exact amount, no change."
        prompt = (
            f"Sale of {_lines_text(sale['items'])}. Bill {rs(total)}. Cash received {rs(cash)}. "
            f"{change_say} Say yes to complete the sale."
        )
        evidence = {"cash_count": notes}
        tendered, kind = cash, "cash"
    else:
        facts["change"] = 0
        prompt = (
            f"Sale of {_lines_text(sale['items'])}. Bill {rs(total)} by UPI. "
            f"Check that {rs(total)} has arrived, then say yes to complete the sale."
        )
        evidence = {"user_confirm": True}
        tendered, kind = total, "upi"

    idem = _key(ctx)
    pay_id = f"P{idem[:10]}"
    proposed_items = [dict(li) for li in sale["items"]]
    change_amt = facts["change"]

    async def commit_sale() -> dict:
        current = await ctx.store.get("sales", sale_id)
        if current and current.get("state") == COMPLETED:
            return ok(f"Sale {sale_id} was already complete.", already_done=True, **_sale_facts(current))
        if current is None or current.get("items") != proposed_items:
            raise ValueError("the sale changed after this was proposed; take the payment again")
        now = time.time()
        done_sale = {**current, "state": COMPLETED, "method": kind, "payment_id": pay_id, "completed_at": now}
        payment = {
            "sale_id": sale_id,
            "direction": "in",
            "method": kind,
            "amount": total,
            "tendered": tendered,
            "change": change_amt,
            "evidence": evidence,
            "ts": now,
        }
        ledger = [
            {
                "sku": li["sku"],
                "delta": -li["qty"],
                "reason": "SALE",
                "ref_type": "sale",
                "ref_id": sale_id,
                "actor": "owner",
            }
            for li in proposed_items
        ]
        await ctx.store.commit(
            idem, ledger=ledger, docs=[("sales", sale_id, done_sale), ("payments", pay_id, payment)]
        )
        stock_after = {li["sku"]: await ctx.store.stock(li["sku"]) for li in proposed_items}
        await ctx.send(State(entity="sale", id=sale_id, state=COMPLETED, summary=f"{rs(total)} {kind}"))
        say = f"Sale complete. {rs(total)} received by {kind}."
        if kind == "cash":
            say += f" Give back {rs(change_amt)} change." if change_amt else " No change due."
        return ok(
            say,
            sale_id=sale_id,
            payment_id=pay_id,
            total=total,
            method=kind,
            tendered=tendered,
            change=change_amt,
            state=COMPLETED,
            stock_after=stock_after,
        )

    return await ctx.confirms.propose(ctx, kind="sale", prompt=prompt, facts=facts, committer=commit_sale)


async def confirm_action(ctx: ToolContext, confirm_id: str = "", answer: str = "") -> dict:
    if not isinstance(confirm_id, str) or not ID_RE.match(confirm_id):
        return err("unknown confirmation")
    if answer not in ("yes", "no"):
        return err("answer must be yes or no")
    return await ctx.confirms.resolve(ctx.session_id, confirm_id, answer)


async def day_summary(ctx: ToolContext) -> dict:
    today = datetime.now(SHOP_TZ).date()
    sales = [
        s
        for s in await ctx.store.query("sales", state=COMPLETED)
        if datetime.fromtimestamp(float(s.get("completed_at", 0)), SHOP_TZ).date() == today
    ]
    revenue = sum(int(s.get("total", 0)) for s in sales)
    by_method = {"cash": 0, "upi": 0}
    qty: dict[str, int] = {}
    names: dict[str, str] = {}
    for s in sales:
        by_method[s.get("method", "cash")] = by_method.get(s.get("method", "cash"), 0) + int(
            s.get("total", 0)
        )
        for li in s.get("items", []):
            qty[li["sku"]] = qty.get(li["sku"], 0) + int(li["qty"])
            names[li["sku"]] = li["name"]
    top = max(qty.items(), key=lambda kv: kv[1]) if qty else None
    facts = {
        "date": today.isoformat(),
        "sales_count": len(sales),
        "revenue": revenue,
        "cash_total": by_method.get("cash", 0),
        "upi_total": by_method.get("upi", 0),
        "items_sold": [
            {"sku": k, "name": names[k], "qty": v} for k, v in sorted(qty.items(), key=lambda kv: -kv[1])
        ],
        "top_seller": {"sku": top[0], "name": names[top[0]], "qty": top[1]} if top else None,
    }
    if not sales:
        return ok("No sales completed today yet.", **facts)
    say = (
        f"Today: {len(sales)} {_plural('sale', len(sales))}, revenue {rs(revenue)} "
        f"({rs(facts['cash_total'])} cash, {rs(facts['upi_total'])} UPI). "
        f"Top seller: {names[top[0]]}, {top[1]} sold."
    )
    return ok(say, **facts)


# ---- schemas -------------------------------------------------------------------------------------
_PRODUCT_OPT = {
    "type": ["string", "null"],
    "description": "Product name, alias (English, Tamil or Hindi) or SKU as the owner said it; "
    "null for all products.",
}
_ITEMS = {
    "type": "array",
    "description": "Products and quantities.",
    "items": obj(
        {
            "product": {"type": "string", "description": "Product name, alias or SKU as the owner said it."},
            "qty": {"type": "integer", "description": "Whole number of units, at least 1."},
        },
        ["product", "qty"],
    ),
}
_SALE_ID = {"type": "string", "description": "sale_id returned by sale_start."}

TOOLS: list[Tool] = [
    Tool(
        name="price_query",
        description="Look up the selling price of one product, or the whole price list when product is null. "
        "Prices are integer rupees.",
        parameters=obj({"product": _PRODUCT_OPT}, ["product"]),
        handler=price_query,
    ),
    Tool(
        name="stock_query",
        description="How many units are on hand for one product, or for every product (low stock first) "
        "when product is null.",
        parameters=obj({"product": _PRODUCT_OPT}, ["product"]),
        handler=stock_query,
    ),
    Tool(
        name="sale_start",
        description="Open a new pending sale with the given items. Returns sale_id, line totals and "
        "the total computed in code. Nothing is written to stock until the sale is paid and confirmed.",
        parameters=obj({"items": _ITEMS}, ["items"]),
        handler=sale_start,
    ),
    Tool(
        name="sale_add",
        description="Add more items to an open pending sale. Returns the updated lines and total.",
        parameters=obj({"sale_id": _SALE_ID, "items": _ITEMS}, ["sale_id", "items"]),
        handler=sale_add,
    ),
    Tool(
        name="sale_pay",
        description="Take payment for a pending sale. cash: the camera reads the notes with two independent "
        "readers, the change is computed in code, then the owner must confirm. upi: the owner checks the "
        "payment arrived and confirms. Returns confirm_id; read the prompt back and wait for "
        "the owner's yes.",
        parameters=obj(
            {"sale_id": _SALE_ID, "method": {"type": "string", "enum": ["cash", "upi"]}},
            ["sale_id", "method"],
        ),
        handler=sale_pay,
        needs_confirm=True,
    ),
    Tool(
        name="confirm_action",
        description="Resolve a pending confirmation with the owner's answer. Only the owner's literal yes "
        "commits the change; anything else cancels it.",
        parameters=obj(
            {
                "confirm_id": {"type": "string", "description": "confirm_id from the proposing tool."},
                "answer": {"type": "string", "enum": ["yes", "no"]},
            },
            ["confirm_id", "answer"],
        ),
        handler=confirm_action,
    ),
    Tool(
        name="day_summary",
        description="Today's completed sales: count, revenue, cash and UPI totals, items sold "
        "and top seller.",
        parameters=obj({}),
        handler=day_summary,
    ),
]
