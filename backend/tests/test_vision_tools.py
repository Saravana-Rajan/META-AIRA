"""Vision tools with a fake store, confirm book and phone. Offline."""
import pytest

from aira import contracts
from aira.tools import vision_tools
from aira.tools.base import FrameData, ToolContext
from aira.vision import invoice, look, sam
from aira.vision.invoice import InvoiceReading
from aira.vision.sam import SamCount

FRAME = FrameData(id="frame-7", jpeg=b"\xff\xd8jpeg", w=640, h=480)
BALL = {"sku": "TB-01", "name": "tennis ball", "sam_noun": "tennis ball", "price": 40}


class FakeStore:
    def __init__(self):
        self.commits, self.on_hand, self.keys = [], 5, set()

    async def find_product(self, name):
        return BALL if name.lower().rstrip("s") == "tennis ball" else None

    async def commit(self, idem_key, *, ledger, docs):
        if idem_key in self.keys:
            return False
        self.keys.add(idem_key)
        self.commits.append((idem_key, ledger, docs))
        self.on_hand += sum(r["delta"] for r in ledger)
        return True

    async def stock(self, sku):
        return self.on_hand


class FakeConfirms:
    def __init__(self):
        self.proposals = []

    async def propose(self, ctx, *, kind, prompt, facts, committer):
        self.proposals.append({"kind": kind, "prompt": prompt, "facts": facts, "committer": committer})
        return {"status": "ok", **facts, "needs_confirm": True, "confirm_id": "c1", "say_en": prompt}


@pytest.fixture
def ctx():
    sent = []

    async def send(msg):
        sent.append(msg)

    async def request_frame(purpose):
        c.purposes.append(purpose)
        return FRAME

    c = ToolContext(
        session_id="s1", shop_id="shop", store=FakeStore(), confirms=FakeConfirms(),
        request_frame=request_frame, send=send, call_id="call-1",
    )
    c.sent = sent
    c.purposes = []
    return c


def boxes(n):
    return [contracts.Box(left=i, top=i, right=i + 5, bottom=i + 5, score=0.9) for i in range(n)]


@pytest.fixture
def count(monkeypatch):
    def set_count(n, ok=True):
        async def fake(frame, noun, min_conf=0.5):
            assert noun == "tennis ball"
            return SamCount(count=n, boxes=boxes(n), ok=ok)

        monkeypatch.setattr(sam, "count_items", fake)

    return set_count


async def test_delivery_check_mismatch_then_commit(ctx, count):
    count(10)
    r = await vision_tools.delivery_check(ctx, product="tennis balls", expected=12)
    assert r["needs_confirm"] and r["counted"] == 10 and r["expected"] == 12 and r["difference"] == -2
    assert r["match"] is False
    assert "10" in r["say_en"] and "12" in r["say_en"] and "2 short" in r["say_en"]
    assert ctx.purposes == ["count"]
    msg = ctx.sent[0]
    assert isinstance(msg, contracts.Count)
    got = (msg.sku, msg.counted, msg.expected, msg.frame_id, len(msg.boxes))
    assert got == ("TB-01", 10, 12, "frame-7", 10)
    prop = ctx.confirms.proposals[0]
    assert prop["kind"] == "delivery"

    key = ctx.idem_key()
    ctx.call_id = "later-call"  # the committer runs on a later turn
    done = await prop["committer"]()
    idem, ledger, docs = ctx.store.commits[0]
    assert idem == key
    assert ledger == [{"sku": "TB-01", "delta": 10, "reason": "DELIVERY", "ref_type": "delivery",
                       "ref_id": r["delivery_id"], "actor": "owner"}]
    assert docs[0][0] == "deliveries" and docs[0][1] == r["delivery_id"] and docs[0][2]["counted"] == 10
    assert done["status"] == "ok" and done["on_hand"] == 15 and "10" in done["say_en"]
    again = await prop["committer"]()
    assert again["already_done"] is True and len(ctx.store.commits) == 1


async def test_delivery_check_no_expected(ctx, count):
    count(12)
    r = await vision_tools.delivery_check(ctx, product="tennis ball", expected=None)
    assert r["match"] is True and r["difference"] is None and "12" in r["say_en"]
    assert ctx.sent[0].expected is None


async def test_delivery_check_unknown_product(ctx, count):
    count(3)
    r = await vision_tools.delivery_check(ctx, product="hockey stick", expected=None)
    assert r["status"] == "no_results" and not ctx.sent and not ctx.confirms.proposals


async def test_delivery_check_zero_and_failure(ctx, count):
    count(0)
    r = await vision_tools.delivery_check(ctx, product="tennis ball", expected=5)
    assert r["status"] == "no_results" and not ctx.confirms.proposals
    count(0, ok=False)
    r = await vision_tools.delivery_check(ctx, product="tennis ball", expected=5)
    assert r["status"] == "error" and r["retryable"]


async def test_delivery_check_no_frame(ctx, count):
    count(3)

    async def no_frame(purpose):
        return None

    ctx.request_frame = no_frame
    r = await vision_tools.delivery_check(ctx, product="tennis ball", expected=None)
    assert r["status"] == "error"


async def test_invoice_read_agreed_and_not(ctx, monkeypatch):
    async def good(frame):
        return invoice.MOCK_RESULT

    monkeypatch.setattr(invoice, "read_invoice", good)
    r = await vision_tools.invoice_read(ctx)
    assert r["status"] == "ok" and r["total"] == 480 and "480" in r["say_en"] and "12" in r["say_en"]
    assert ctx.purposes == ["invoice"]

    async def bad(frame):
        return InvoiceReading(agreed=False, supplier=None, disagreements=["dates differ"])

    monkeypatch.setattr(invoice, "read_invoice", bad)
    r = await vision_tools.invoice_read(ctx)
    assert r["status"] == "no_results" and r["agreed"] is False and "total" not in r


async def test_camera_ask(ctx, monkeypatch):
    async def fake_look(frame, question):
        assert question == "what is this?"
        return "A red cricket ball."

    monkeypatch.setattr(look, "look", fake_look)
    r = await vision_tools.camera_ask(ctx, question="what is this?")
    assert r["status"] == "ok" and r["answer"] == "A red cricket ball." and r["verified"] is False
    assert ctx.purposes == ["ask"]


def test_tool_schemas_strict():
    names = {t.name for t in vision_tools.TOOLS}
    assert names == {"camera_ask", "invoice_read", "delivery_check"}
    for t in vision_tools.TOOLS:
        p = t.parameters
        assert p["additionalProperties"] is False and set(p["required"]) == set(p["properties"])
        assert t.schema()["function"]["name"] == t.name
    dc = next(t for t in vision_tools.TOOLS if t.name == "delivery_check")
    assert dc.needs_confirm and dc.parameters["properties"]["expected"]["type"] == ["integer", "null"]
