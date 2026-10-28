import itertools
import sys
from types import ModuleType, SimpleNamespace

import pytest

from aira.confirm import ConfirmBook
from aira.contracts import Confirm, State
from aira.store import MemoryStore
from aira.tools import shop
from aira.tools.base import FrameData, ToolContext

BY_NAME = {t.name: t for t in shop.TOOLS}
_calls = itertools.count(1)


def make_ctx(store=None, confirms=None, session_id="s1") -> ToolContext:
    sent: list = []
    frames: list = []

    async def send(msg):
        sent.append(msg)

    async def request_frame(purpose):
        frames.append(purpose)
        return FrameData(id="f1", jpeg=b"\xff\xd8fake", w=640, h=480)

    ctx = ToolContext(
        session_id=session_id,
        shop_id="t",
        store=store or MemoryStore("t"),
        confirms=confirms or ConfirmBook(),
        request_frame=request_frame,
        send=send,
    )
    ctx.sent = sent  # type: ignore[attr-defined]
    ctx.frames = frames  # type: ignore[attr-defined]
    return ctx


async def call(ctx: ToolContext, name: str, **args) -> dict:
    """Like the agent loop: a fresh call_id per tool call."""
    ctx.call_id = f"call_{next(_calls)}"
    return await BY_NAME[name].handler(ctx, **args)


@pytest.fixture
def cash(monkeypatch):
    """Install a fake aira.vision.cash module; tests set .reading."""
    mod = ModuleType("aira.vision.cash")
    state = SimpleNamespace(reading=None, calls=0)

    async def read_cash(frame):
        state.calls += 1
        return state.reading

    mod.read_cash = read_cash  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "aira.vision.cash", mod)
    return state


def reading(total, notes=None, agreed=True, hint=None):
    return SimpleNamespace(agreed=agreed, total=total, notes=notes or {}, hint=hint)


# ---- schemas -------------------------------------------------------------------------------------
def _check_strict(schema: dict):
    if schema.get("type") == "object":
        assert schema["additionalProperties"] is False
        assert sorted(schema["required"]) == sorted(schema["properties"])
        for sub in schema["properties"].values():
            _check_strict(sub)
    if schema.get("type") == "array":
        _check_strict(schema["items"])


def test_tool_schemas_strict():
    assert set(BY_NAME) == {
        "price_query",
        "stock_query",
        "sale_start",
        "sale_add",
        "sale_pay",
        "confirm_action",
        "day_summary",
    }
    for t in shop.TOOLS:
        assert t.description and t.schema()["function"]["name"] == t.name
        _check_strict(t.parameters)
    assert BY_NAME["sale_pay"].needs_confirm


# ---- queries -------------------------------------------------------------------------------------
async def test_price_query():
    ctx = make_ctx()
    r = await call(ctx, "price_query", product="tennis balls")
    assert r["status"] == "ok" and r["price"] == 60 and "₹60" in r["say_en"]
    r = await call(ctx, "price_query", product=None)
    assert len(r["products"]) == 10 and "₹7500" in r["say_en"]
    r = await call(ctx, "price_query", product="football")
    assert r["status"] == "no_results"


async def test_stock_query():
    ctx = make_ctx()
    r = await call(ctx, "stock_query", product="helmet")
    assert r["on_hand"] == 4 and r["low"] is False and "4" in r["say_en"]
    await ctx.store.commit(
        "k",
        ledger=[
            {"sku": "HLM-01", "delta": -3, "reason": "SALE", "ref_type": "sale", "ref_id": "x", "actor": "t"}
        ],
        docs=[],
    )
    r = await call(ctx, "stock_query", product=None)
    assert r["stock"][0]["sku"] == "HLM-01" and r["stock"][0]["low"] is True
    assert "Low stock: helmet 1" in r["say_en"]


# ---- sale flow -----------------------------------------------------------------------------------
async def test_full_cash_sale_flow(cash):
    ctx = make_ctx()
    r = await call(ctx, "sale_start", items=[{"product": "tennis balls", "qty": 3}])
    assert r["status"] == "ok" and r["total"] == 180
    sale_id = r["sale_id"]
    r = await call(
        ctx,
        "sale_add",
        sale_id=sale_id,
        items=[{"product": "bat grip", "qty": 1}, {"product": "tennis ball", "qty": 1}],
    )
    assert r["total"] == 4 * 60 + 120 == 360
    assert {i["sku"]: i["qty"] for i in r["items"]} == {"TB-01": 4, "GRP-01": 1}
    assert "₹360" in r["say_en"]

    cash.reading = reading(500, {200: 2, 100: 1})
    r = await call(ctx, "sale_pay", sale_id=sale_id, method="cash")
    assert ctx.frames == ["cash"] and cash.calls == 1
    assert r["status"] == "ok" and r["needs_confirm"] is True
    assert r["total"] == 360 and r["cash_received"] == 500 and r["change"] == 140
    assert "₹140" in r["say_en"] and "₹360" in r["say_en"]
    assert any(isinstance(m, Confirm) for m in ctx.sent)
    cid = r["confirm_id"]
    assert await ctx.store.stock("TB-01") == 40  # nothing written before yes

    r1 = await call(ctx, "confirm_action", confirm_id=cid, answer="yes")
    r2 = await call(ctx, "confirm_action", confirm_id=cid, answer="yes")
    assert r1["status"] == "ok" and r1["change"] == 140 and r1["stock_after"] == {"TB-01": 36, "GRP-01": 24}
    assert r2["already_done"] is True
    assert await ctx.store.stock("TB-01") == 36
    assert await ctx.store.stock("GRP-01") == 24

    sale = await ctx.store.get("sales", sale_id)
    assert sale["state"] == "SALE_COMPLETED" and sale["method"] == "cash"
    payments = await ctx.store.query("payments", sale_id=sale_id)
    assert len(payments) == 1 and payments[0]["amount"] == 360 and payments[0]["change"] == 140
    assert payments[0]["evidence"] == {"cash_count": {"200": 2, "100": 1}}
    ledger = await ctx.store.query("ledger", ref_id=sale_id)
    assert sorted((row["sku"], row["delta"]) for row in ledger) == [("GRP-01", -1), ("TB-01", -4)]
    assert all(row["reason"] == "SALE" for row in ledger)
    assert any(isinstance(m, State) and m.state == "SALE_COMPLETED" for m in ctx.sent)

    # paying again for a completed sale does not propose anything new
    r = await call(ctx, "sale_pay", sale_id=sale_id, method="cash")
    assert r["already_done"] is True and "confirm_id" not in r

    s = await call(ctx, "day_summary")
    assert s["sales_count"] == 1 and s["revenue"] == 360 and s["cash_total"] == 360
    assert s["top_seller"]["sku"] == "TB-01" and s["top_seller"]["qty"] == 4
    assert "₹360" in s["say_en"]


async def test_cash_disagreement_gives_hint(cash):
    ctx = make_ctx()
    sale_id = (await call(ctx, "sale_start", items=[{"product": "leather ball", "qty": 1}]))["sale_id"]
    cash.reading = reading(None, agreed=False, hint="Spread the notes apart and show them again.")
    r = await call(ctx, "sale_pay", sale_id=sale_id, method="cash")
    assert r["status"] == "no_results" and "Spread the notes" in r["say_en"]
    assert "confirm_id" not in r and ctx.confirms.pending("s1") == []


async def test_cash_short(cash):
    ctx = make_ctx()
    sale_id = (await call(ctx, "sale_start", items=[{"product": "helmet", "qty": 1}]))["sale_id"]
    cash.reading = reading(2000, {500: 4})
    r = await call(ctx, "sale_pay", sale_id=sale_id, method="cash")
    assert r["paid"] is False and r["short_by"] == 200 and "₹200 short" in r["say_en"]
    assert "confirm_id" not in r


async def test_upi_sale_and_no():
    ctx = make_ctx()
    sale_id = (await call(ctx, "sale_start", items=[{"product": "stumps", "qty": 2}]))["sale_id"]
    r = await call(ctx, "sale_pay", sale_id=sale_id, method="upi")
    assert r["needs_confirm"] and r["total"] == 1900 and "₹1900" in r["say_en"]
    no = await call(ctx, "confirm_action", confirm_id=r["confirm_id"], answer="no")
    assert no["rejected"] is True
    assert await ctx.store.stock("STM-01") == 6

    r = await call(ctx, "sale_pay", sale_id=sale_id, method="upi")
    yes = await call(ctx, "confirm_action", confirm_id=r["confirm_id"], answer="yes")
    assert yes["status"] == "ok" and yes["method"] == "upi"
    assert await ctx.store.stock("STM-01") == 4
    pay = (await ctx.store.query("payments", sale_id=sale_id))[0]
    assert pay["evidence"] == {"user_confirm": True}


async def test_sale_errors():
    ctx = make_ctx()
    r = await call(ctx, "sale_start", items=[{"product": "football", "qty": 1}])
    assert r["status"] == "no_results" and r["unknown"] == ["football"]
    r = await call(ctx, "sale_start", items=[{"product": "english willow bat", "qty": 5}])
    assert r["status"] == "error" and r["in_stock"] == 4
    r = await call(ctx, "sale_start", items=[{"product": "helmet", "qty": 0}])
    assert r["status"] == "error"
    r = await call(ctx, "sale_add", sale_id="bad id!", items=[{"product": "helmet", "qty": 1}])
    assert r["status"] == "error"
    r = await call(ctx, "sale_pay", sale_id="S_missing", method="cash")
    assert r["status"] == "error"
    r = await call(ctx, "confirm_action", confirm_id="c_nope", answer="yes")
    assert r["status"] == "error"


async def test_confirm_from_other_session_is_unknown():
    store, book = MemoryStore("t"), ConfirmBook()
    owner, other = make_ctx(store, book, "s1"), make_ctx(store, book, "s2")
    sale_id = (await call(owner, "sale_start", items=[{"product": "pads", "qty": 1}]))["sale_id"]
    cid = (await call(owner, "sale_pay", sale_id=sale_id, method="upi"))["confirm_id"]
    r = await call(other, "confirm_action", confirm_id=cid, answer="yes")
    assert r["status"] == "error"
    assert await store.stock("PAD-01") == 5


async def test_stock_ran_out_before_yes_keeps_pending():
    ctx = make_ctx()
    sale_id = (await call(ctx, "sale_start", items=[{"product": "english bat", "qty": 3}]))["sale_id"]
    cid = (await call(ctx, "sale_pay", sale_id=sale_id, method="upi"))["confirm_id"]
    await ctx.store.commit(
        "other",
        ledger=[
            {"sku": "BAT-EW", "delta": -2, "reason": "SALE", "ref_type": "sale", "ref_id": "x", "actor": "t"}
        ],
        docs=[],
    )
    r = await call(ctx, "confirm_action", confirm_id=cid, answer="yes")
    assert r["status"] == "error" and r["still_pending"] is True
    assert await ctx.store.stock("BAT-EW") == 2
    assert (await ctx.store.get("sales", sale_id))["state"] == "SALE_PENDING"


async def test_day_summary_empty():
    r = await call(make_ctx(), "day_summary")
    assert r["status"] == "ok" and r["sales_count"] == 0 and r["revenue"] == 0


async def test_idem_key_captured_at_propose_time(cash):
    """The committer must use the key from the sale_pay call, not ctx.idem_key() at yes time."""
    store, book = MemoryStore("t"), ConfirmBook()
    ctx = make_ctx(store, book)
    sale_id = (await call(ctx, "sale_start", items=[{"product": "leather ball", "qty": 2}]))["sale_id"]
    cash.reading = reading(1000, {500: 2})
    r = await call(ctx, "sale_pay", sale_id=sale_id, method="cash")
    assert r["change"] == 100
    cid = r["confirm_id"]
    ctx.call_id = "yes_turn_1"
    first = await book.resolve(ctx.session_id, cid, "yes")
    ctx.call_id = "yes_turn_2"
    second = await book.resolve(ctx.session_id, cid, "yes")
    assert first["status"] == "ok" and second["already_done"] is True
    assert await store.stock("LB-01") == 13
    ledger = await store.query("ledger", ref_id=sale_id)
    assert len(ledger) == 1 and len({row["idem_key"] for row in ledger}) == 1
    assert ctx.results == []  # tools never append to ctx.results
