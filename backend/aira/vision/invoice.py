"""Two-reader supplier invoice reading (PLAN 11.4).

Reader A extracts plainly; reader B transcribes row by row. Lines are paired by name similarity
(>= 0.75) and compared on qty, unit price and line total. Supplier names must match (>= 0.85), dates
must match, and the bill's own arithmetic must hold to +-1 rupee. A handwritten bill is a
disagreement. The invoice is used only if there are zero disagreements.

Disagreement messages deliberately contain no numbers: unverified figures must never reach speech.
"""
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from aira.config import get_settings
from aira.tools.base import FrameData
from aira.vision.readers import ReadResult, read_both

NAME_SIM = 0.75
SUPPLIER_SIM = 0.85
TOLERANCE = 1.0  # rupees


@dataclass
class InvoiceReading:
    agreed: bool
    supplier: str | None
    lines: list[dict] = field(default_factory=list)
    total: int | None = None
    disagreements: list[str] = field(default_factory=list)


MOCK_RESULT = InvoiceReading(
    agreed=True,
    supplier="Sri Murugan Sports Traders",
    lines=[{"name": "Tennis ball", "qty": 12, "unit_price": 40, "line_total": 480}],
    total=480,
    disagreements=[],
)

_NUM_NULL = {"type": ["number", "null"]}
_STR_NULL = {"type": ["string", "null"]}
_LINE_PROPS = {
    "name": {"type": "string"},
    "qty": {"type": "integer"},
    "unit_price": {"type": "number"},
    "line_total": {"type": "number"},
}
_TOP_PROPS = {
    "supplier": _STR_NULL,
    "date": {**_STR_NULL, "description": "invoice date as YYYY-MM-DD, or null"},
    "total": {**_NUM_NULL, "description": "grand total printed on the bill, rupees"},
    "handwritten": {"type": "boolean", "description": "true if the bill is handwritten"},
}


def _schema(list_key: str, extra_line: dict) -> dict:
    line_props = {**extra_line, **_LINE_PROPS}
    return {
        "type": "object",
        "properties": {
            **_TOP_PROPS,
            list_key: {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": line_props,
                    "required": list(line_props),
                    "additionalProperties": False,
                },
            },
        },
        "required": [*_TOP_PROPS, list_key],
        "additionalProperties": False,
    }


SCHEMA_A = _schema("lines", {})
SCHEMA_B = _schema("rows", {"row": {"type": "integer", "description": "1-based row number"}})

_COMMON = (
    "This is a supplier invoice photographed for a blind shopkeeper in India. Amounts are in rupees. "
    "Copy text and numbers exactly as printed; never guess or fill in missing values (use null). "
    "Set handwritten to true if the bill is handwritten."
)
PROMPT_A = _COMMON + (
    " Extract the supplier name, date, each item line (name, quantity, unit price, line total)"
    " and the grand total."
)
PROMPT_B = _COMMON + (
    " Transcribe the item table row by row, top to bottom, numbering each row,"
    " copying each cell exactly; then the supplier name, date and the grand total."
)


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, " ".join(a.lower().split()), " ".join(b.lower().split())).ratio()


def _close(x: float | None, y: float | None, tol: float = 0.01) -> bool:
    if x is None or y is None:
        return x is y
    return abs(float(x) - float(y)) <= tol


def _rupees(x: float) -> int:
    return int(round(float(x)))


def _norm_date(d: str | None) -> str | None:
    if d is None:
        return None
    d = "".join(d.split()).lower()
    return d or None


def _lines(data: dict, key: str) -> list[dict] | None:
    rows = data.get(key)
    if not isinstance(rows, list):
        return None
    out = []
    for r in rows:
        if not isinstance(r, dict) or not isinstance(r.get("name"), str):
            return None
        try:
            out.append(
                {
                    "name": r["name"].strip(),
                    "qty": int(r["qty"]),
                    "unit_price": float(r["unit_price"]),
                    "line_total": float(r["line_total"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            return None
    return out


def judge(a: ReadResult, b: ReadResult) -> InvoiceReading:
    """Compare the two readings and apply the arithmetic checks."""
    dis: list[str] = []
    if not (a.ok and a.data is not None):
        dis.append("first reader failed")
    if not (b.ok and b.data is not None):
        dis.append("second reader failed")
    if dis:
        return InvoiceReading(agreed=False, supplier=None, disagreements=dis)
    da, db = a.data or {}, b.data or {}
    la, lb = _lines(da, "lines"), _lines(db, "rows")
    if la is None or lb is None:
        return InvoiceReading(agreed=False, supplier=None, disagreements=["malformed reading"])

    if da.get("handwritten") or db.get("handwritten"):
        dis.append("the bill is handwritten")

    sa, sb = da.get("supplier"), db.get("supplier")
    if not sa or not sb:
        dis.append("supplier name not readable")
    elif _sim(sa, sb) < SUPPLIER_SIM:
        dis.append("supplier names differ")

    if _norm_date(da.get("date")) != _norm_date(db.get("date")):
        dis.append("dates differ")

    if not la or not lb:
        dis.append("no item lines read")

    # Pair lines greedily by best name similarity.
    unpaired_b = list(range(len(lb)))
    for line in la:
        best, best_sim = None, 0.0
        for j in unpaired_b:
            s = _sim(line["name"], lb[j]["name"])
            if s > best_sim:
                best, best_sim = j, s
        if best is None or best_sim < NAME_SIM:
            dis.append(f"line '{line['name']}' not found by second reader")
            continue
        unpaired_b.remove(best)
        other = lb[best]
        if line["qty"] != other["qty"]:
            dis.append(f"quantity differs for '{line['name']}'")
        if not _close(line["unit_price"], other["unit_price"]):
            dis.append(f"unit price differs for '{line['name']}'")
        if not _close(line["line_total"], other["line_total"]):
            dis.append(f"line total differs for '{line['name']}'")
    for j in unpaired_b:
        dis.append(f"line '{lb[j]['name']}' not found by first reader")

    ta, tb = da.get("total"), db.get("total")
    if ta is None or tb is None:
        dis.append("grand total not readable")
    elif not _close(ta, tb):
        dis.append("grand totals differ")

    # The bill's own arithmetic (reader A's numbers; they matched reader B's above).
    for line in la:
        if abs(line["qty"] * line["unit_price"] - line["line_total"]) > TOLERANCE:
            dis.append(f"quantity times price does not match the line total for '{line['name']}'")
    if ta is not None and la and abs(sum(x["line_total"] for x in la) - float(ta)) > TOLERANCE:
        dis.append("line totals do not add up to the grand total")

    if dis:
        return InvoiceReading(agreed=False, supplier=None, lines=[], total=None, disagreements=dis)
    lines = [
        {
            "name": x["name"],
            "qty": x["qty"],
            "unit_price": _rupees(x["unit_price"]),
            "line_total": _rupees(x["line_total"]),
        }
        for x in la
    ]
    return InvoiceReading(agreed=True, supplier=sa.strip(), lines=lines, total=_rupees(ta), disagreements=[])


async def read_invoice(frame: FrameData) -> InvoiceReading:
    if get_settings().mock:
        return MOCK_RESULT
    a, b = await read_both(
        frame,
        prompt_a=PROMPT_A,
        schema_a=("invoice_extract", SCHEMA_A),
        prompt_b=PROMPT_B,
        schema_b=("invoice_rows", SCHEMA_B),
    )
    return judge(a, b)
