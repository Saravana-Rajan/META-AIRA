"""Safety guard run after every agent turn (PLAN 11.3).

Every money amount and every count spoken next to a unit or product word must come from a tool result
(this or recent turns) or from the catalog prices. Hedged counts ("about 10 balls") are allowed.
Results flagged {"external": true} (web search) never count as evidence.
"""
import re
from collections.abc import Iterable

# ---- number words -> digits ------------------------------------------------------------------------
_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
    "ninety": 90,
}
_MULT = {"hundred": 100, "thousand": 1000, "lakh": 100_000, "lakhs": 100_000}
_NUMBER_WORDS = {**_UNITS, **_TENS, **_MULT}

_TOKEN = re.compile(r"[A-Za-z]+|\d+")


def _value(tokens: list[str]) -> int:
    total, current = 0, 0
    for t in tokens:
        if t.isdigit():
            current += int(t)
        elif t in _UNITS or t in _TENS:
            current += _NUMBER_WORDS[t]
        elif t == "hundred":
            current = max(current, 1) * 100
        elif t in _MULT:
            total += max(current, 1) * _MULT[t]
            current = 0
    return total + current


def words_to_digits(text: str) -> str:
    """'two hundred and fifty rupees' -> '250 rupees'; '2 thousand' -> '2000'. Ordinals are left alone."""
    toks = list(_TOKEN.finditer(text))
    out, last, i = [], 0, 0
    while i < len(toks):
        word = toks[i].group().lower()
        is_digit = word.isdigit()
        if not (word in _NUMBER_WORDS or is_digit):
            i += 1
            continue
        group = [i]
        j = i + 1
        while j < len(toks):
            gap = text[toks[j - 1].end():toks[j].start()]
            if gap.strip(" -") != "":
                break
            nxt = toks[j].group().lower()
            prev = toks[group[-1]].group().lower()
            if nxt in _NUMBER_WORDS and not nxt.isdigit():
                # A digit may only be followed by a multiplier ("2 thousand"), never by "five".
                if prev.isdigit() and nxt not in _MULT:
                    break
                # "twenty five" ok, "five twenty" / "ten ten" not merged.
                if prev in _UNITS and nxt in _UNITS | _TENS or prev in _TENS and nxt in _TENS:
                    break
                group.append(j)
                j += 1
                continue
            if nxt == "and" and prev in _MULT and j + 1 < len(toks):
                after = toks[j + 1].group().lower()
                if after in _UNITS or after in _TENS:
                    group.append(j)
                    j += 1
                    continue
            break
        words = [toks[k].group().lower() for k in group]
        if is_digit and len(group) == 1:
            i = j
            continue
        if words[0] in _MULT:  # a bare "hundred" without a number before it
            i = j
            continue
        out.append(text[last:toks[group[0]].start()])
        out.append(str(_value([w for w in words if w != "and"])))
        last = toks[group[-1]].end()
        i = j
    out.append(text[last:])
    return "".join(out)


# ---- spoken amounts ------------------------------------------------------------------------------
_MONEY_BEFORE = re.compile(r"(₹|\brs\.?|\binr|\brupees?|ரூ\.?|रु\.?)\s*$", re.IGNORECASE)
_MONEY_AFTER = re.compile(
    r"^\s*(/-|rupees?\b|rs\b\.?|inr\b|bucks\b|ரூபாய்|ரூ|रुपये|रुपए|रुपया|रुपैया|रु|rupaye\b|rupay\b)",
    re.IGNORECASE,
)
_COUNT_UNITS = {
    "piece", "pieces", "pc", "pcs", "pack", "packs", "packet", "packets", "set", "sets", "item", "items",
    "unit", "units", "box", "boxes", "pair", "pairs", "dozen",
    "பீஸ்", "பாக்கெட்", "செட்", "पीस", "पैकेट", "सेट", "नग",
}
_COUNT_AFTER = re.compile(r"^\s+(left|remaining|in stock|on hand|counted)\b", re.IGNORECASE)
_HEDGES = {
    "about", "around", "roughly", "approximately", "approx", "nearly", "almost", "maybe", "perhaps",
    "estimated", "சுமார்", "கிட்டத்தட்ட", "நிறைய", "लगभग", "करीब", "तकरीबन", "क़रीब",
}
_NUM = re.compile(r"\d+(?:\.\d+)?")
_PUNCT = ".,!?;:()\"'“”‘’"
_STOP = {"the", "and", "with", "for", "set", "of", "a", "an", "new", "old"}


def _product_vocab(catalog: Iterable[dict]) -> set[str]:
    vocab: set[str] = set()

    def add(phrase: str) -> None:
        for w in str(phrase).lower().split():
            w = w.strip(_PUNCT)
            if len(w) >= 3 and w not in _STOP and not w.isdigit():
                vocab.update({w, w + "s", w + "es"})

    for p in catalog:
        add(p.get("name", ""))
        add(p.get("sam_noun", ""))
        aliases = p.get("aliases") or {}
        for lst in aliases.values() if isinstance(aliases, dict) else [aliases]:
            for a in lst or []:
                add(a)
    return vocab


def _next_word(text: str) -> str:
    m = re.match(r"\s+(\S+)", text)
    return m.group(1).strip(_PUNCT).lower() if m else ""


def _prev_words(text: str, n: int = 2) -> list[str]:
    return [w.strip(_PUNCT).lower() for w in text.split()[-n:]]


def _to_num(raw: str) -> float | int:
    v = float(raw)
    return int(v) if v.is_integer() else v


def spoken_numbers(spoken: str, catalog: Iterable[dict] = ()) -> list[tuple[str, float | int, bool]]:
    """Money amounts and unit/product counts in `spoken` as (snippet, value, hedged).

    The snippet is prefixed with its kind: 'money:...' or 'count:...'."""
    text = words_to_digits(spoken)
    text = re.sub(r"(?<=\d),(?=\d)", "", text)
    vocab = _product_vocab(catalog) | _COUNT_UNITS
    found: list[tuple[str, float | int, bool]] = []
    for m in _NUM.finditer(text):
        before, after = text[: m.start()], text[m.end():]
        money = bool(_MONEY_BEFORE.search(before) or _MONEY_AFTER.match(after))
        if not money:
            # glued to letters/digits ("1st", "5pm", "S1234") or part of a time ("5:30") -> not a quantity
            if after[:1].isalnum() or (before[-1:].isalnum()):
                continue
            if after[:1] == ":" and after[1:2].isdigit() or before[-1:] == ":" and before[-2:-1].isdigit():
                continue
            if after[:1] == "%":
                continue
        count = not money and (_next_word(after) in vocab or bool(_COUNT_AFTER.match(after)))
        if not (money or count):
            continue
        hedged = count and any(w in _HEDGES for w in _prev_words(before))
        end = m.end() + (len(after) - len(after.lstrip()))
        nxt = re.match(r"\S+", text[end:])
        cur = _MONEY_BEFORE.search(before)
        prefix = (cur.group(1) + ("" if cur.group(1) == "₹" else " ")) if cur else ""
        snippet = (prefix + m.group() + ((" " + nxt.group()) if nxt else "")).strip()
        found.append((("money:" if money else "count:") + snippet.strip(_PUNCT), _to_num(m.group()), hedged))
    return found


# ---- evidence --------------------------------------------------------------------------------------
def _is_id_key(key: str) -> bool:
    k = key.lower()
    return k == "id" or k.endswith("_id") or k.endswith("id_") or k in {"idem_key", "ts", "seq"}


def evidence_numbers(results: Iterable, catalog: Iterable[dict] = ()) -> set[float | int]:
    allowed: set[float | int] = set()

    def walk(v, key: str = "") -> None:
        if isinstance(v, dict):
            if v.get("external") is True:
                return
            for k, x in v.items():
                walk(x, str(k))
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x, key)
        elif isinstance(v, bool) or v is None:
            return
        elif isinstance(v, (int, float)):
            allowed.add(_to_num(str(v)) if isinstance(v, float) else v)
        elif isinstance(v, str) and not _is_id_key(key):
            s = re.sub(r"(?<=\d),(?=\d)", "", words_to_digits(v))
            for n in _NUM.findall(s):
                allowed.add(_to_num(n))

    for r in results:
        walk(r)
    for p in catalog:
        price = p.get("price")
        if isinstance(price, (int, float)) and not isinstance(price, bool):
            allowed.add(_to_num(str(price)))
    return allowed


def check(spoken: str, results: list[dict], catalog: list[dict]) -> list[str]:
    """Return one violation string per unsupported money amount or unhedged count in `spoken`."""
    if not spoken:
        return []
    allowed = evidence_numbers(results, catalog)
    violations: list[str] = []
    for snippet, value, hedged in spoken_numbers(spoken, catalog):
        kind, _, said = snippet.partition(":")
        if hedged or value in allowed:
            continue
        what = "amount" if kind == "money" else "count"
        msg = f"you said the {what} '{said}', which is not in any tool result"
        if msg not in violations:
            violations.append(msg)
    return violations
