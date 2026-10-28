"""Demo shop catalog (data/catalog.json) and product-name matching shared by every Store."""

import copy
import difflib
import json
import re
from functools import lru_cache
from pathlib import Path

CATALOG_PATH = Path(__file__).parent / "data" / "catalog.json"
FUZZY_CUTOFF = 0.8


@lru_cache
def _load() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def load_catalog() -> dict:
    """A fresh deep copy of the whole catalog: shop, products, inventory, suppliers."""
    return copy.deepcopy(_load())


def shop_info() -> dict:
    return load_catalog()["shop"]


def seed_products() -> list[dict]:
    return load_catalog()["products"]


def seed_inventory() -> dict[str, int]:
    return load_catalog()["inventory"]


def suppliers() -> list[dict]:
    return load_catalog()["suppliers"]


def keywords(products: list[dict] | None = None) -> list[str]:
    """Catalog terms for speech-recognition biasing (names + every alias), de-duplicated, in order."""
    seen: dict[str, None] = {}
    for p in products if products is not None else seed_products():
        seen.setdefault(p["name"], None)
        for terms in p.get("aliases", {}).values():
            for t in terms:
                seen.setdefault(t, None)
    return list(seen)


# ---- matching -----------------------------------------------------------------------------------
_SPACE = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s-]", re.UNICODE)


def normalize(text: str) -> str:
    return _SPACE.sub(" ", _PUNCT.sub(" ", text.casefold())).strip()


def _singular_word(word: str) -> str:
    if len(word) <= 3 or not word.isascii():
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("ches", "shes", "sses", "xes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def singular(text: str) -> str:
    """Singularise the last word ("tennis balls" -> "tennis ball", "bats" -> "bat")."""
    words = text.split(" ")
    if not words or not words[-1]:
        return text
    words[-1] = _singular_word(words[-1])
    return " ".join(words)


def product_terms(p: dict) -> list[str]:
    terms = [p["sku"], p["name"]]
    for lang_terms in p.get("aliases", {}).values():
        terms.extend(lang_terms)
    return [normalize(t) for t in terms if t]


def match_product(products: list[dict], query: str) -> dict | None:
    """Exact (case-insensitive, plural-tolerant) match on sku/name/aliases, then a difflib fuzzy fallback."""
    q = normalize(query)
    if not q:
        return None
    variants = {q, singular(q)}
    index: list[tuple[str, dict]] = []
    for p in products:
        for t in product_terms(p):
            index.append((t, p))
    # 1. exact, either side singularised
    for t, p in index:
        if t in variants or singular(t) in variants:
            return p
    # 2. every query word appears in a term of exactly one product ("willow bats" -> only bats match)
    qwords = {singular(w) for w in q.split(" ")}
    hits = {p["sku"]: p for t, p in index if qwords <= {singular(w) for w in t.split(" ")}}
    if len(hits) == 1:
        return next(iter(hits.values()))
    # 3. fuzzy over all terms (and their singular forms)
    choices: dict[str, dict] = {}
    for t, p in index:
        choices.setdefault(t, p)
        choices.setdefault(singular(t), p)
    best: tuple[float, dict] | None = None
    for v in variants:
        for hit in difflib.get_close_matches(v, list(choices), n=1, cutoff=FUZZY_CUTOFF):
            score = difflib.SequenceMatcher(None, v, hit).ratio()
            if best is None or score > best[0]:
                best = (score, choices[hit])
    return best[1] if best else None
