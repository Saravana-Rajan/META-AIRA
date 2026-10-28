import asyncio
from types import SimpleNamespace

from aira.confirm import ConfirmBook
from aira.contracts import Confirm
from aira.store import MemoryStore


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def make_ctx(session_id: str = "s1"):
    sent: list = []

    async def send(msg):
        sent.append(msg)

    return SimpleNamespace(session_id=session_id, send=send, store=MemoryStore("t"), sent=sent)


def counting_committer(result: dict | None = None, delay: float = 0.0):
    calls = {"n": 0}

    async def committer() -> dict:
        calls["n"] += 1
        if delay:
            await asyncio.sleep(delay)
        return result or {"status": "ok", "say_en": "Done.", "total": 120}

    return committer, calls


async def test_propose_sends_card_and_returns_facts():
    book, ctx = ConfirmBook(), make_ctx()
    committer, _ = counting_committer()
    out = await book.propose(
        ctx, kind="sale", prompt="Bill ₹120. Say yes.", facts={"total": 120}, committer=committer
    )
    assert out["status"] == "ok" and out["needs_confirm"] is True and out["total"] == 120
    assert out["say_en"] == "Bill ₹120. Say yes."
    card = ctx.sent[0]
    assert isinstance(card, Confirm) and card.id == out["confirm_id"] and card.kind == "sale"
    assert book.pending("s1")[0]["confirm_id"] == out["confirm_id"]
    assert (await ctx.store.get("proposals", out["confirm_id"]))["state"] == "pending"


async def test_yes_commits_once_even_concurrently():
    book, ctx = ConfirmBook(), make_ctx()
    committer, calls = counting_committer(delay=0.01)
    cid = (await book.propose(ctx, kind="sale", prompt="p", facts={}, committer=committer))["confirm_id"]
    a, b = await asyncio.gather(book.resolve("s1", cid, "yes"), book.resolve("s1", cid, "yes"))
    assert calls["n"] == 1
    assert {bool(a.get("already_done")), bool(b.get("already_done"))} == {False, True}
    assert a["status"] == b["status"] == "ok"
    again = await book.resolve("s1", cid, "yes")
    assert again["already_done"] is True and again["total"] == 120
    assert book.pending("s1") == []


async def test_no_rejects():
    book, ctx = ConfirmBook(), make_ctx()
    committer, calls = counting_committer()
    cid = (await book.propose(ctx, kind="sale", prompt="p", facts={}, committer=committer))["confirm_id"]
    out = await book.resolve("s1", cid, "no")
    assert out["rejected"] is True and out["reason"] == "no"
    out = await book.resolve("s1", cid, "yes")  # a later yes cannot revive it
    assert out["rejected"] is True
    assert calls["n"] == 0


async def test_expiry():
    clock = Clock()
    book, ctx = ConfirmBook(ttl_s=120, clock=clock), make_ctx()
    committer, calls = counting_committer()
    cid = (await book.propose(ctx, kind="sale", prompt="p", facts={}, committer=committer))["confirm_id"]
    clock.t += 121
    assert book.pending("s1") == []
    out = await book.resolve("s1", cid, "yes")
    assert out["rejected"] is True and out["reason"] == "expired"
    assert calls["n"] == 0


async def test_other_session_unknown():
    book, ctx = ConfirmBook(), make_ctx("s1")
    committer, calls = counting_committer()
    cid = (await book.propose(ctx, kind="sale", prompt="p", facts={}, committer=committer))["confirm_id"]
    out = await book.resolve("s2", cid, "yes")
    assert out == {"status": "error", "message": "unknown confirmation", "retryable": False}
    assert (await book.resolve("s1", "c_nope", "yes"))["message"] == "unknown confirmation"
    assert calls["n"] == 0 and book.pending("s2") == [] and len(book.pending("s1")) == 1


async def test_committer_failure_keeps_pending():
    book, ctx = ConfirmBook(), make_ctx()
    state = {"fail": True, "n": 0}

    async def committer() -> dict:
        state["n"] += 1
        if state["fail"]:
            raise RuntimeError("db down")
        return {"status": "ok", "say_en": "Done."}

    cid = (await book.propose(ctx, kind="stock", prompt="p", facts={}, committer=committer))["confirm_id"]
    out = await book.resolve("s1", cid, "yes")
    assert out["status"] == "error" and out["retryable"] is True and "db down" in out["message"]
    assert len(book.pending("s1")) == 1
    state["fail"] = False
    out = await book.resolve("s1", cid, "yes")
    assert out["status"] == "ok" and not out.get("already_done")
    assert state["n"] == 2


async def test_reject_all():
    book = ConfirmBook()
    ctx1, ctx2 = make_ctx("s1"), make_ctx("s2")
    committer, calls = counting_committer()
    c1 = (await book.propose(ctx1, kind="sale", prompt="p", facts={}, committer=committer))["confirm_id"]
    c2 = (await book.propose(ctx2, kind="sale", prompt="p", facts={}, committer=committer))["confirm_id"]
    book.reject_all("s1")
    assert book.pending("s1") == [] and len(book.pending("s2")) == 1
    assert (await book.resolve("s1", c1, "yes"))["rejected"] is True
    assert (await book.resolve("s2", c2, "yes"))["status"] == "ok"
    assert calls["n"] == 1


async def test_unknown_kind_maps_to_generic_card():
    book, ctx = ConfirmBook(), make_ctx()
    committer, _ = counting_committer()
    await book.propose(ctx, kind="refund", prompt="p", facts={}, committer=committer)
    assert ctx.sent[0].kind == "generic"
