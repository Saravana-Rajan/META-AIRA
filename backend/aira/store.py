"""Shop data store: the Store protocol, an in-memory implementation and the factory.

Layout (mirrors Firestore `shops/{shop_id}/...`): `products/{sku}`, `inventory/{sku}` (`on_hand`),
`sales`, `payments`, `deliveries`, `ledger`, `proposals`, `events`. Money is integer rupees.
"""

import asyncio
import copy
import time
from typing import Protocol, runtime_checkable

from aira import catalog
from aira.config import get_settings

LEDGER_REASONS = ("SALE", "DELIVERY", "ADJUST")


@runtime_checkable
class Store(Protocol):
    async def products(self) -> list[dict]: ...
    async def product(self, sku: str) -> dict | None: ...
    async def find_product(self, name: str) -> dict | None: ...
    async def stock(self, sku: str) -> int: ...
    async def stock_all(self) -> dict[str, int]: ...
    async def get(self, collection: str, doc_id: str) -> dict | None: ...
    async def put(self, collection: str, doc_id: str, data: dict) -> None: ...
    async def query(self, collection: str, **equals) -> list[dict]: ...
    async def commit(
        self, idem_key: str, *, ledger: list[dict], docs: list[tuple[str, str, dict]]
    ) -> bool: ...
    async def events(self, after: int = 0) -> list[dict]: ...
    async def add_event(self, kind: str, data: dict) -> None: ...


def validate_ledger(rows: list[dict]) -> None:
    """Shape rules shared by every Store implementation (raises ValueError)."""
    for r in rows:
        if not isinstance(r.get("sku"), str) or not r["sku"]:
            raise ValueError(f"ledger row without sku: {r!r}")
        delta = r.get("delta")
        if not isinstance(delta, int) or isinstance(delta, bool):
            raise ValueError(f"ledger delta must be int: {r!r}")
        reason = r.get("reason")
        if reason not in LEDGER_REASONS:
            raise ValueError(f"bad ledger reason: {reason!r}")
        if reason == "SALE" and delta >= 0:
            raise ValueError("SALE ledger delta must be negative")
        if reason == "DELIVERY" and delta <= 0:
            raise ValueError("DELIVERY ledger delta must be positive")


def net_deltas(rows: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[r["sku"]] = out.get(r["sku"], 0) + r["delta"]
    return out


class MemoryStore:
    """In-process store for tests, mock mode and local demos. Safe under concurrent asyncio tasks."""

    def __init__(self, shop_id: str = "", seed: bool = True):
        self.shop_id = shop_id or get_settings().shop_id
        self._lock = asyncio.Lock()
        self._docs: dict[str, dict[str, dict]] = {}
        self._commits: set[str] = set()
        self._events: list[dict] = []
        self._seq = 0
        self._order: list[str] = []
        if seed:
            self._seed()

    def _seed(self) -> None:
        cat = catalog.load_catalog()
        self._docs["shop"] = {"info": {"id": "info", **cat["shop"]}}
        for p in cat["products"]:
            self._col("products")[p["sku"]] = p
            self._order.append(p["sku"])
        for sku, n in cat["inventory"].items():
            self._col("inventory")[sku] = {"sku": sku, "on_hand": int(n)}
        for s in cat["suppliers"]:
            self._col("suppliers")[s["id"]] = s

    def _col(self, collection: str) -> dict[str, dict]:
        return self._docs.setdefault(collection, {})

    # ---- catalog -------------------------------------------------------------------------------
    async def products(self) -> list[dict]:
        col = self._col("products")
        ordered = [col[s] for s in self._order if s in col] + [
            p for s, p in col.items() if s not in self._order
        ]
        return [copy.deepcopy(p) for p in ordered if p.get("active", True)]

    async def product(self, sku: str) -> dict | None:
        p = self._col("products").get(sku)
        return copy.deepcopy(p) if p else None

    async def find_product(self, name: str) -> dict | None:
        return catalog.match_product(await self.products(), name)

    # ---- stock ---------------------------------------------------------------------------------
    async def stock(self, sku: str) -> int:
        inv = self._col("inventory").get(sku)
        return int(inv["on_hand"]) if inv else 0

    async def stock_all(self) -> dict[str, int]:
        inv = self._col("inventory")
        return {p["sku"]: int(inv.get(p["sku"], {}).get("on_hand", 0)) for p in await self.products()}

    # ---- generic docs --------------------------------------------------------------------------
    async def get(self, collection: str, doc_id: str) -> dict | None:
        d = self._col(collection).get(doc_id)
        return copy.deepcopy(d) if d is not None else None

    async def put(self, collection: str, doc_id: str, data: dict) -> None:
        self._col(collection)[doc_id] = {"id": doc_id, **copy.deepcopy(data)}

    async def query(self, collection: str, **equals) -> list[dict]:
        return [
            copy.deepcopy(d)
            for d in self._col(collection).values()
            if all(d.get(k) == v for k, v in equals.items())
        ]

    # ---- atomic commit -------------------------------------------------------------------------
    async def commit(self, idem_key: str, *, ledger: list[dict], docs: list[tuple[str, str, dict]]) -> bool:
        if not idem_key:
            raise ValueError("idem_key required")
        validate_ledger(ledger)
        async with self._lock:
            if idem_key in self._commits:
                return False
            inv = self._col("inventory")
            products = self._col("products")
            new_stock: dict[str, int] = {}
            for sku, delta in net_deltas(ledger).items():
                if sku not in products:
                    raise ValueError(f"unknown sku {sku}")
                after = int(inv.get(sku, {}).get("on_hand", 0)) + delta
                if after < 0:
                    raise ValueError(f"stock for {sku} would go below 0 ({after})")
                new_stock[sku] = after
            # validation passed: apply everything (no awaits below, so nothing interleaves)
            ts = time.time()
            for sku, after in new_stock.items():
                inv[sku] = {"sku": sku, "on_hand": after}
            for i, row in enumerate(ledger):
                row_id = f"{idem_key}-{i}"
                doc = {"id": row_id, **copy.deepcopy(row), "idem_key": idem_key, "ts": ts}
                self._col("ledger")[row_id] = doc
                self._emit("ledger", {**doc, "on_hand": new_stock[row["sku"]]}, ts)
            for collection, doc_id, data in docs:
                doc = {"id": doc_id, **copy.deepcopy(data)}
                self._col(collection)[doc_id] = doc
                self._emit("doc", {"collection": collection, "id": doc_id, "data": copy.deepcopy(doc)}, ts)
            self._commits.add(idem_key)
            self._col("commits")[idem_key] = {"id": idem_key, "ts": ts}
            return True

    # ---- dashboard events ----------------------------------------------------------------------
    def _emit(self, kind: str, data: dict, ts: float | None = None) -> None:
        self._seq += 1
        self._events.append({"seq": self._seq, "kind": kind, "data": data, "ts": ts or time.time()})

    async def events(self, after: int = 0) -> list[dict]:
        return [copy.deepcopy(e) for e in self._events if e["seq"] > after]

    async def add_event(self, kind: str, data: dict) -> None:
        self._emit(kind, copy.deepcopy(data))


_stores: dict[str, Store] = {}


def make_store(shop_id: str) -> Store:
    """New store for a shop, chosen by settings.store ("memory" | "firestore"); seeded from catalog.json."""
    if get_settings().store == "firestore":
        from aira.firestore_store import FirestoreStore

        return FirestoreStore(shop_id)
    return MemoryStore(shop_id)


def get_store(shop_id: str) -> Store:
    """Process-wide shared store per shop (so all sessions and the dashboard see the same memory store)."""
    if shop_id not in _stores:
        _stores[shop_id] = make_store(shop_id)
    return _stores[shop_id]
