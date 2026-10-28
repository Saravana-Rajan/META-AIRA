"""Two-reader cash reading (PLAN 11.4).

Reader A groups the money as {denomination, count}; reader B lists every note/coin's face value.
They agree only if the per-denomination counts match exactly. Code computes the total.
We never judge whether a note is genuine.
"""
from collections import Counter
from dataclasses import dataclass, field

from aira.config import get_settings
from aira.tools.base import FrameData
from aira.vision.readers import ReadResult, read_both

NOTES = frozenset({10, 20, 50, 100, 200, 500})
COINS = frozenset({1, 2, 5, 10, 20})
VALID = NOTES | COINS

HINT_SPREAD = "spread the notes apart so none overlap, then show again"
HINT_AGAIN = "show the money again, flat and in good light"
HINT_UNSUPPORTED = "I can read only current Indian rupee notes and coins; remove the other one and show again"
HINT_NONE = "I could not see any money; show it again"


@dataclass
class CashReading:
    agreed: bool
    total: int | None
    notes: dict[int, int] = field(default_factory=dict)
    hint: str | None = None


MOCK_RESULT = CashReading(agreed=True, total=500, notes={500: 1}, hint=None)

_FLAGS = {
    "overlap": {"type": "boolean", "description": "true if any notes or coins overlap or are hidden"},
    "other_currency": {"type": "boolean", "description": "true if any money is not Indian rupees"},
}

SCHEMA_A = {
    "type": "object",
    "properties": {
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"denomination": {"type": "integer"}, "count": {"type": "integer"}},
                "required": ["denomination", "count"],
                "additionalProperties": False,
            },
        },
        **_FLAGS,
    },
    "required": ["groups", "overlap", "other_currency"],
    "additionalProperties": False,
}

SCHEMA_B = {
    "type": "object",
    "properties": {
        "values": {"type": "array", "items": {"type": "integer"}},
        **_FLAGS,
    },
    "required": ["values", "overlap", "other_currency"],
    "additionalProperties": False,
}

_COMMON = (
    "You are reading Indian rupee cash held up to a camera for a blind shopkeeper. "
    "Read only the printed face value of each note or coin. Do not judge whether money is genuine. "
    "If notes or coins overlap or are partly hidden, set overlap to true. "
    "If any money is not Indian rupees, set other_currency to true. "
    "Report exactly what you see; never guess."
)
PROMPT_A = _COMMON + " Group the money by face value: one entry per denomination with how many there are."
PROMPT_B = _COMMON + (
    " Go one by one, left to right, and list the face value of every single note and coin "
    "(a value appears once per note)."
)


def _counts_a(data: dict) -> Counter | None:
    groups = data.get("groups")
    if not isinstance(groups, list):
        return None
    c: Counter = Counter()
    for g in groups:
        if not isinstance(g, dict):
            return None
        d, n = g.get("denomination"), g.get("count")
        if type(d) is not int or type(n) is not int or n < 0:
            return None
        if n:
            c[d] += n
    return c


def _counts_b(data: dict) -> Counter | None:
    values = data.get("values")
    if not isinstance(values, list) or any(type(v) is not int for v in values):
        return None
    return Counter(values)


def _fail(hint: str) -> CashReading:
    return CashReading(agreed=False, total=None, notes={}, hint=hint)


def judge(a: ReadResult, b: ReadResult) -> CashReading:
    """Apply the agreement rules to two readings."""
    if not (a.ok and b.ok and a.data is not None and b.data is not None):
        return _fail(HINT_AGAIN)
    ca, cb = _counts_a(a.data), _counts_b(b.data)
    if ca is None or cb is None:
        return _fail(HINT_AGAIN)  # malformed output
    if a.data.get("other_currency") or b.data.get("other_currency"):
        return _fail(HINT_UNSUPPORTED)
    if any(d not in VALID for d in (*ca, *cb)):
        return _fail(HINT_UNSUPPORTED)  # e.g. the withdrawn 2000 note
    if a.data.get("overlap") or b.data.get("overlap"):
        return _fail(HINT_SPREAD)
    if not ca and not cb:
        return _fail(HINT_NONE)
    if ca != cb:
        return _fail(HINT_SPREAD)
    notes = dict(sorted(ca.items(), reverse=True))
    return CashReading(agreed=True, total=sum(d * n for d, n in notes.items()), notes=notes, hint=None)


async def read_cash(frame: FrameData) -> CashReading:
    if get_settings().mock:
        return MOCK_RESULT
    a, b = await read_both(
        frame,
        prompt_a=PROMPT_A,
        schema_a=("cash_grouped", SCHEMA_A),
        prompt_b=PROMPT_B,
        schema_b=("cash_each_note", SCHEMA_B),
    )
    return judge(a, b)
