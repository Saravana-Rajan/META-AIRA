"""Deterministic rule-based agent for offline demos and tests (no network, no model).

Maps a few English phrases (plus Tamil/Hindi yes/no) to the real tool handlers and speaks the tool's say_en.
"""
import logging
import re
from typing import Any

from aira.safety import words_to_digits
from aira.tools.base import Tool, ToolContext, err

log = logging.getLogger(__name__)

_YES = {"yes", "yeah", "yep", "yup", "ok", "okay", "confirm", "sure", "haan", "haa", "han", "ha", "ஆம்", "ஆமா",
        "ஆமாம்", "சரி", "हाँ", "हां", "हा", "ठीक"}
_NO = {"no", "nope", "cancel", "nahi", "nahin", "na", "illa", "illai", "இல்லை", "வேண்டாம்", "नहीं", "ना"}
_PUNCT = ".,!?;:\"'"
_FILLER = re.compile(
    r"\b(please|now|today|for me|in stock|do we have|do i have|are there|left|is it)\b", re.I
)


def _clean_product(s: str) -> str:
    s = _FILLER.sub(" ", s)
    s = re.sub(r"^\s*(the|a|an|some|of|for)\s+", "", s.strip(), flags=re.I)
    return re.sub(r"\s+", " ", s).strip(_PUNCT + " ")


def parse_items(text: str) -> list[dict]:
    """'2 tennis balls and 1 grip' -> [{product: tennis balls, qty: 2}, {product: grip, qty: 1}]"""
    items = []
    for part in re.split(r",|\band\b|&|\+", words_to_digits(text)):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d+)\s*(?:x\s+)?(.+)$", part)
        qty, name = (int(m.group(1)), m.group(2)) if m else (1, part)
        name = _clean_product(name)
        if name:
            items.append({"product": name, "qty": qty})
    return items


class MockAgent:
    def __init__(self, ctx: ToolContext, tools: list[Tool], store: Any = None):
        self.ctx = ctx
        self.store = store
        self.tools = {t.name: t for t in tools}
        self.open_sale_id: str | None = None
        self.pending: list[tuple[str, str]] = []  # (confirm_id, tool name), newest last
        self._n = 0

    async def _call(self, name: str, **args: Any) -> dict:
        tool = self.tools.get(name)
        self._n += 1
        self.ctx.call_id = f"mock-{self.ctx.session_id}-{self._n}"
        if tool is None:
            result = err(f"unknown tool {name}")
        else:
            try:
                result = await tool.handler(self.ctx, **args)
            except Exception as e:  # noqa: BLE001 - a demo must keep talking
                log.exception("mock tool %s failed", name)
                result = err(f"{name} failed: {e}")
        if not isinstance(result, dict):
            result = err(f"{name} returned no result")
        self.ctx.results.append(result)
        self._track(name, result)
        return result

    def _track(self, name: str, result: dict) -> None:
        if result.get("sale_id") and name in ("sale_start", "sale_add"):
            self.open_sale_id = str(result["sale_id"])
        cid = result.get("confirm_id")
        if cid and result.get("needs_confirm"):
            self.pending = [p for p in self.pending if p[0] != cid] + [(str(cid), name)]

    @staticmethod
    def _say(result: dict) -> str:
        if result.get("say_en"):
            return str(result["say_en"])
        if result.get("status") == "error":
            return f"Sorry, {result.get('message', 'that did not work')}."
        return "Done."

    async def _confirm(self, answer: str) -> str:
        if not self.pending:
            return "There is nothing waiting for a yes or no."
        cid, origin = self.pending[-1]
        result = await self._call("confirm_action", confirm_id=cid, answer=answer)
        if result.get("status") != "error" or answer == "no":
            self.pending = [p for p in self.pending if p[0] != cid]
        if answer == "yes" and result.get("status") == "ok" and origin == "sale_pay":
            self.open_sale_id = None
        if answer == "no" and origin in ("sale_start", "sale_add"):
            self.open_sale_id = None
        if result.get("say_en") or result.get("status") == "error":
            return self._say(result)
        return "Done." if answer == "yes" else "Cancelled."

    async def turn(self, user_text: str) -> str:
        raw = user_text.strip()
        text = words_to_digits(raw).lower().strip()
        words = [w.strip(_PUNCT) for w in text.split()]
        if not words:
            return "I am listening."

        first = words[0]
        if first in _YES or raw.strip(_PUNCT) in _YES:
            return await self._confirm("yes")
        if first in _NO or raw.strip(_PUNCT) in _NO:
            return await self._confirm("no")

        if m := re.search(r"\b(?:check|count)\s+(?:the\s+)?deliver(?:y|ies)(?:\s+of)?\s*(\d+)?\s*(.*)$",
                          text):
            args: dict[str, Any] = {"product": _clean_product(m.group(2))}
            if m.group(1):
                args["expected"] = int(m.group(1))
            return self._say(await self._call("delivery_check", **args))

        if re.search(r"\bwhat is this\b|\bwhat's this\b|\blook\b|\bwhat do you see\b|\bread this\b", text):
            return self._say(await self._call("camera_ask", question=raw))

        if re.search(r"\bsummary\b|\bhow was today\b|\btoday'?s sales\b", text):
            return self._say(await self._call("day_summary"))

        if re.search(r"\bupi\b|\bgpay\b|\bphonepe\b", text) or re.search(r"\bcash\b", text):
            method = "upi" if re.search(r"\bupi\b|\bgpay\b|\bphonepe\b", text) else "cash"
            if not self.open_sale_id:
                return "There is no open sale to pay for."
            return self._say(await self._call("sale_pay", sale_id=self.open_sale_id, method=method))

        if m := re.match(r"^(?:sell|sale of|sale|bill)\s+(.+)$", text):
            items = parse_items(m.group(1))
            if not items:
                return "Which items should I sell?"
            return self._say(await self._call("sale_start", items=items))

        if m := re.match(r"^add\s+(.+?)(?:\s+to\s+(?:the\s+)?(?:sale|bill))?$", text):
            items = parse_items(m.group(1))
            if not items:
                return "Which items should I add?"
            if self.open_sale_id:
                return self._say(await self._call("sale_add", sale_id=self.open_sale_id, items=items))
            return self._say(await self._call("sale_start", items=items))

        if re.search(r"\bprice list\b|^prices?\??$|\ball prices\b", text):
            return self._say(await self._call("price_query"))
        if m := re.search(r"\b(?:price|cost|rate)\s+(?:of|for)\s+(.+)$", text) or re.search(
            r"\bhow much (?:is|are|does|for)\s+(.+?)(?:\s+cost)?$", text
        ):
            return self._say(await self._call("price_query", product=_clean_product(m.group(1))))

        if m := re.search(r"\bhow many\s+(.+)$", text) or re.search(r"\bstock (?:of|for)\s+(.+)$", text):
            product = _clean_product(m.group(1))
            return self._say(await self._call("stock_query", **({"product": product} if product else {})))
        if re.search(r"\bstock\b", text):
            return self._say(await self._call("stock_query"))

        return "Sorry, I did not catch that. You can ask a price, the stock, or start a sale."
