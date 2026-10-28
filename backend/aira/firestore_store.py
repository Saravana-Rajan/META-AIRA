"""Firestore-backed Store (same interface as store.MemoryStore).

Everything lives under `shops/{shop_id}/...` in database settings.firestore_db. google-cloud-firestore
is imported lazily so tests and mock mode never need GCP credentials.
"""

import asyncio
import time
from typing import Any

from aira import catalog
from aira.config import get_settings
from aira.store import net_deltas, validate_ledger

EVENTS_LIMIT = 500


def _firestore():
    from google.cloud import firestore

    return firestore


def _field_filter(field: str, op: str, value: Any):
    from google.cloud.firestore_v1.base_query import FieldFilter

    return FieldFilter(field, op, value)


class FirestoreStore:
    def __init__(self, shop_id: str = "", client: Any = None):
        self.shop_id = shop_id or get_settings().shop_id
        self._client = client
        self._seeded = False
        self._seed_lock = asyncio.Lock()

    # ---- plumbing ------------------------------------------------------------------------------
    @property
    def client(self):
        if self._client is None:
            s = get_settings()
            self._client = _firestore().AsyncClient(project=s.gcp_project, database=s.firestore_db)
        return self._client

    def _root(self):
        return self.client.collection("shops").document(self.shop_id)

    def _col(self, collection: str):
        return self._root().collection(collection)

    async def _ensure_seeded(self) -> None:
        """Write the demo catalog + opening stock the first time this shop is used."""
        if self._seeded:
            return
        async with self._seed_lock:
            if self._seeded:
                return
            snap = await self._root().get()
            if not snap.exists:
                cat = catalog.load_catalog()
                batch = self.client.batch()
                batch.set(self._root(), {**cat["shop"], "id": self.shop_id, "created": time.time()})
                for i, p in enumerate(cat["products"]):
                    batch.set(self._col("products").document(p["sku"]), {**p, "order": i})
                for sku, n in cat["inventory"].items():
                    batch.set(self._col("inventory").document(sku), {"sku": sku, "on_hand": int(n)})
                for sup in cat["suppliers"]:
                    batch.set(self._col("suppliers").document(sup["id"]), sup)
                batch.set(self._col("meta").document("counters"), {"event_seq": 0})
                await batch.commit()
            self._seeded = True

    # ---- catalog -------------------------------------------------------------------------------
    async def products(self) -> list[dict]:
        await self._ensure_seeded()
        docs = [d.to_dict() async for d in self._col("products").stream()]
        docs = [d for d in docs if d and d.get("active", True)]
        docs.sort(key=lambda d: d.get("order", 1_000_000))
        return [{k: v for k, v in d.items() if k != "order"} for d in docs]

    async def product(self, sku: str) -> dict | None:
        d = await self.get("products", sku)
        if d:
            d.pop("order", None)
        return d

    async def find_product(self, name: str) -> dict | None:
        return catalog.match_product(await self.products(), name)

    # ---- stock ---------------------------------------------------------------------------------
    async def stock(self, sku: str) -> int:
        d = await self.get("inventory", sku)
        return int(d.get("on_hand", 0)) if d else 0

    async def stock_all(self) -> dict[str, int]:
        await self._ensure_seeded()
        inv = {
            d.id: int((d.to_dict() or {}).get("on_hand", 0)) async for d in self._col("inventory").stream()
        }
        return {p["sku"]: inv.get(p["sku"], 0) for p in await self.products()}

    # ---- generic docs --------------------------------------------------------------------------
    async def get(self, collection: str, doc_id: str) -> dict | None:
        await self._ensure_seeded()
        snap = await self._col(collection).document(doc_id).get()
        return snap.to_dict() if snap.exists else None

    async def put(self, collection: str, doc_id: str, data: dict) -> None:
        await self._ensure_seeded()
        await self._col(collection).document(doc_id).set({"id": doc_id, **data})

    async def query(self, collection: str, **equals) -> list[dict]:
        await self._ensure_seeded()
        q = self._col(collection)
        for k, v in equals.items():
            q = q.where(filter=_field_filter(k, "==", v))
        return [d.to_dict() async for d in q.stream()]

    # ---- atomic commit -------------------------------------------------------------------------
    async def commit(self, idem_key: str, *, ledger: list[dict], docs: list[tuple[str, str, dict]]) -> bool:
        if not idem_key:
            raise ValueError("idem_key required")
        validate_ledger(ledger)
        await self._ensure_seeded()
        fs = _firestore()
        deltas = net_deltas(ledger)
        commit_ref = self._col("commits").document(idem_key)
        out: dict[str, Any] = {}

        @fs.async_transactional
        async def run(tx) -> bool:
            # all reads first (Firestore transaction rule)
            if (await commit_ref.get(transaction=tx)).exists:
                return False
            stock_after: dict[str, int] = {}
            for sku, delta in deltas.items():
                prod = await self._col("products").document(sku).get(transaction=tx)
                if not prod.exists:
                    raise ValueError(f"unknown sku {sku}")
                inv = await self._col("inventory").document(sku).get(transaction=tx)
                on_hand = int((inv.to_dict() or {}).get("on_hand", 0)) if inv.exists else 0
                if on_hand + delta < 0:
                    raise ValueError(f"stock for {sku} would go below 0 ({on_hand + delta})")
                stock_after[sku] = on_hand + delta
            ts = time.time()
            for sku, after in stock_after.items():
                tx.set(self._col("inventory").document(sku), {"sku": sku, "on_hand": after})
            rows = []
            for i, row in enumerate(ledger):
                row_id = f"{idem_key}-{i}"
                doc = {"id": row_id, **row, "idem_key": idem_key, "ts": ts}
                tx.set(self._col("ledger").document(row_id), doc)
                rows.append({**doc, "on_hand": stock_after[row["sku"]]})
            written = []
            for collection, doc_id, data in docs:
                doc = {"id": doc_id, **data}
                tx.set(self._col(collection).document(doc_id), doc)
                written.append({"collection": collection, "id": doc_id, "data": doc})
            tx.set(commit_ref, {"id": idem_key, "ts": ts})
            out["events"] = [("ledger", r) for r in rows] + [("doc", w) for w in written]
            return True

        done = await run(self.client.transaction())
        if done:
            await self._append_events(out["events"])
        return done

    # ---- dashboard events ----------------------------------------------------------------------
    async def _append_events(self, items: list[tuple[str, dict]]) -> None:
        if not items:
            return
        fs = _firestore()
        counter = self._col("meta").document("counters")

        @fs.async_transactional
        async def run(tx) -> None:
            snap = await counter.get(transaction=tx)
            seq = int((snap.to_dict() or {}).get("event_seq", 0)) if snap.exists else 0
            ts = time.time()
            for kind, data in items:
                seq += 1
                tx.set(
                    self._col("events").document(f"{seq:012d}"),
                    {"seq": seq, "kind": kind, "data": data, "ts": ts},
                )
            tx.set(counter, {"event_seq": seq}, merge=True)

        await run(self.client.transaction())

    async def events(self, after: int = 0) -> list[dict]:
        await self._ensure_seeded()
        q = (
            self._col("events")
            .where(filter=_field_filter("seq", ">", after))
            .order_by("seq")
            .limit(EVENTS_LIMIT)
        )
        return [d.to_dict() async for d in q.stream()]

    async def add_event(self, kind: str, data: dict) -> None:
        await self._ensure_seeded()
        await self._append_events([(kind, data)])
