"""Two-reader invoice: pairing, comparison, arithmetic. Offline."""
import json
from types import SimpleNamespace

from aira.config import Settings
from aira.tools.base import FrameData
from aira.vision import invoice, readers
from aira.vision.readers import ReadResult

FRAME = FrameData(id="f1", jpeg=b"\xff\xd8jpeg", w=640, h=480)
SUP = "Sri Murugan Sports Traders"


def ra(lines, supplier=SUP, total=880, date="2026-10-28", handwritten=False):
    return {"supplier": supplier, "date": date, "lines": lines, "total": total, "handwritten": handwritten}


def rb(rows, supplier=SUP, total=880, date="2026-10-28", handwritten=False):
    rows = [{"row": i + 1, **r} for i, r in enumerate(rows)]
    return {"supplier": supplier, "date": date, "rows": rows, "total": total, "handwritten": handwritten}


def L(name, qty, price, total):
    return {"name": name, "qty": qty, "unit_price": price, "line_total": total}


BALLS = [L("Tennis ball", 12, 40, 480)]


def judge(a, b):
    return invoice.judge(ReadResult(ok=True, data=a), ReadResult(ok=True, data=b))


def test_agree_with_reordered_rows():
    a = [L("Tennis ball", 12, 40, 480), L("Cricket bat (Kashmir willow)", 1, 400, 400)]
    b = [L("Cricket bat (Kashmir wilow)", 1, 400.0, 400.0), L("Tennis Ball", 12, 40.0, 480.0)]
    r = judge(ra(a), rb(b, supplier="Sri Murugan Sports Trader"))
    assert r.agreed, r.disagreements
    assert r.total == 880 and r.supplier == SUP
    assert r.lines[0] == {"name": "Tennis ball", "qty": 12, "unit_price": 40, "line_total": 480}


def test_name_below_similarity_is_unpaired():
    a = [L("Tennis ball", 12, 40, 480), L("Cricket bat", 1, 400, 400)]
    b = [L("Tennis ball", 12, 40, 480), L("Batting gloves", 1, 400, 400)]
    r = judge(ra(a), rb(b))
    assert not r.agreed
    assert any("not found by second reader" in d for d in r.disagreements)
    assert any("not found by first reader" in d for d in r.disagreements)
    assert r.lines == [] and r.total is None and r.supplier is None


def test_qty_mismatch():
    r = judge(ra(BALLS, total=480), rb([L("Tennis ball", 10, 40, 480)], total=480))
    assert not r.agreed and any("quantity differs" in d for d in r.disagreements)


def test_arithmetic_tolerance():
    line = [L("Tennis ball", 3, 33.5, 101)]  # 100.5 vs 101: within one rupee
    ok = judge(ra(line, total=101), rb(line, total=101))
    assert ok.agreed, ok.disagreements
    bad = judge(ra(BALLS, total=500), rb(BALLS, total=500))
    assert not bad.agreed and any("add up" in d for d in bad.disagreements)
    wrong_line = [L("Tennis ball", 12, 40, 400)]
    bad_line = judge(ra(wrong_line, total=400), rb(wrong_line, total=400))
    assert any("quantity times price" in d for d in bad_line.disagreements)


def test_supplier_date_handwritten():
    r = judge(ra(BALLS, total=480), rb(BALLS, total=480, supplier="Kumar Traders"))
    assert any("supplier" in d for d in r.disagreements)
    r = judge(ra(BALLS, total=480), rb(BALLS, total=480, date="2026-10-27"))
    assert any("dates" in d for d in r.disagreements)
    r = judge(ra(BALLS, total=480, handwritten=True), rb(BALLS, total=480))
    assert not r.agreed and any("handwritten" in d for d in r.disagreements)


def test_disagreements_carry_no_numbers():
    r = judge(ra(BALLS, total=480), rb([L("Tennis ball", 10, 45, 450)], total=450))
    assert r.disagreements and not any(ch.isdigit() for d in r.disagreements for ch in d)


def test_reader_failure():
    r = invoice.judge(ReadResult(ok=False, error="timeout"), ReadResult(ok=True, data=rb([])))
    assert not r.agreed and r.disagreements == ["first reader failed"]


async def test_read_invoice_end_to_end(monkeypatch):
    s = Settings(aira_mock=False, model_api_key="k", reader2_base_url="", reader2_model="")
    monkeypatch.setattr(invoice, "get_settings", lambda: s)
    monkeypatch.setattr(readers, "get_settings", lambda: s)
    by_model = {s.model_for("read"): ra(BALLS, total=480), s.model_for("verify"): rb(BALLS, total=480)}

    async def create(**kw):
        content = json.dumps(by_model[kw["model"]])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(readers, "meta_client", lambda: client)
    monkeypatch.setattr(readers, "reader2_client", lambda: client)
    r = await invoice.read_invoice(FRAME)
    assert r.agreed and r.total == 480


async def test_mock(monkeypatch):
    monkeypatch.setattr(invoice, "get_settings", lambda: Settings(aira_mock=True))
    r = await invoice.read_invoice(FRAME)
    assert r.agreed and r.lines[0]["qty"] == 12 and r.total == 480


def test_schemas_strict():
    def walk(schema):
        if schema.get("type") == "object":
            assert schema["additionalProperties"] is False
            assert set(schema["required"]) == set(schema["properties"])
            for sub in schema["properties"].values():
                walk(sub)
        if schema.get("type") == "array":
            walk(schema["items"])

    walk(invoice.SCHEMA_A)
    walk(invoice.SCHEMA_B)
