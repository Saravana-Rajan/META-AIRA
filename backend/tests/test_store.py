import asyncio

import pytest

from aira import catalog
from aira.config import get_settings
from aira.store import MemoryStore, Store, make_store


def sale_row(sku: str, qty: int) -> dict:
    return {"sku": sku, "delta": -qty, "reason": "SALE", "ref_type": "sale", "ref_id": "S1", "actor": "owner"}


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore("test-shop")


def test_catalog_shape():
    cat = catalog.load_catalog()
    assert cat["shop"]["name"] == "AIRA Cricket Store"
    assert 8 <= len(cat["products"]) <= 12
    assert len(cat["suppliers"]) == 2
    for p in cat["products"]:
        for k in (
            "sku",
            "name",
            "brand",
            "unit",
            "price",
            "cost_price",
            "reorder_threshold",
            "look",
            "sam_noun",
        ):
            assert k in p, (p["sku"], k)
        assert isinstance(p["price"], int) and isinstance(p["cost_price"], int)
        assert p["cost_price"] < p["price"]
        assert p["unit"] in ("piece", "pack", "set")
        assert set(p["aliases"]) == {"en", "ta", "hi"} and all(p["aliases"].values())
        assert p["sku"] in cat["inventory"]
    assert "tennis ball" in catalog.keywords()


async def test_seeded(store):
    assert isinstance(store, Store)
    prods = await store.products()
    assert prods[0]["sku"] == "TB-01"
    assert await store.stock("TB-01") == 40
    assert (await store.stock_all())["BAT-EW"] == 4
    assert (await store.product("LB-01"))["price"] == 450
    assert await store.product("NOPE") is None


@pytest.mark.parametrize(
    "query,sku",
    [
        ("tennis ball", "TB-01"),
        ("Tennis Balls", "TB-01"),
        ("tb-01", "TB-01"),
        ("kashmir bats", "BAT-KW"),
        ("English Willow Bats", "BAT-EW"),
        ("gloves", "GLV-01"),
        ("helmets", "HLM-01"),
        ("stumps", "STM-01"),
        ("டென்னிஸ் பந்து", "TB-01"),
        ("sivappu pandhu", "LB-01"),
        ("कश्मीरी बल्ला", "BAT-KW"),
        ("tenis bal", "TB-01"),  # fuzzy
        ("helmit", "HLM-01"),  # fuzzy
        ("ball pack", "TB-06"),
    ],
)
async def test_find_product(store, query, sku):
    p = await store.find_product(query)
    assert p is not None and p["sku"] == sku


async def test_find_product_none(store):
    assert await store.find_product("football") is None
    assert await store.find_product("") is None


async def test_generic_docs(store):
    await store.put("sales", "S1", {"state": "SALE_PENDING", "total": 60})
    await store.put("sales", "S2", {"state": "SALE_COMPLETED", "total": 90})
    assert (await store.get("sales", "S1"))["total"] == 60
    assert [d["id"] for d in await store.query("sales", state="SALE_COMPLETED")] == ["S2"]
    assert await store.get("sales", "missing") is None
    got = await store.get("sales", "S1")
    got["total"] = 0  # copies, not live references
    assert (await store.get("sales", "S1"))["total"] == 60


async def test_commit_idempotent(store):
    docs = [("sales", "S1", {"state": "SALE_COMPLETED", "total": 120})]
    assert await store.commit("k1", ledger=[sale_row("TB-01", 2)], docs=docs) is True
    assert await store.commit("k1", ledger=[sale_row("TB-01", 2)], docs=docs) is False
    assert await store.stock("TB-01") == 38
    ledger = await store.query("ledger")
    assert len(ledger) == 1 and ledger[0]["idem_key"] == "k1" and "ts" in ledger[0]
    assert (await store.get("sales", "S1"))["state"] == "SALE_COMPLETED"


async def test_commit_concurrent_same_key(store):
    results = await asyncio.gather(
        *[store.commit("k", ledger=[sale_row("LB-01", 1)], docs=[]) for _ in range(10)]
    )
    assert sum(results) == 1
    assert await store.stock("LB-01") == 14


async def test_commit_negative_stock_applies_nothing(store):
    with pytest.raises(ValueError):
        await store.commit(
            "k2",
            ledger=[sale_row("TB-01", 1), sale_row("BAT-EW", 5)],
            docs=[("sales", "S9", {"state": "SALE_COMPLETED"})],
        )
    assert await store.stock("TB-01") == 40
    assert await store.stock("BAT-EW") == 4
    assert await store.get("sales", "S9") is None
    assert await store.query("ledger") == []
    assert await store.events() == []
    # key was not burned: a valid retry with the same key still works
    assert await store.commit("k2", ledger=[sale_row("BAT-EW", 4)], docs=[]) is True
    assert await store.stock("BAT-EW") == 0


async def test_commit_rejects_bad_rows(store):
    with pytest.raises(ValueError):
        await store.commit("k3", ledger=[{**sale_row("TB-01", 1), "delta": 1}], docs=[])
    with pytest.raises(ValueError):
        await store.commit("k4", ledger=[sale_row("NOPE", 1)], docs=[])
    with pytest.raises(ValueError):
        await store.commit("k5", ledger=[{**sale_row("TB-01", 1), "reason": "THEFT"}], docs=[])


async def test_events_seq(store):
    await store.add_event("hello", {"a": 1})
    await store.commit("k6", ledger=[sale_row("GRP-01", 1)], docs=[("payments", "P1", {"amount": 120})])
    ev = await store.events()
    assert [e["seq"] for e in ev] == [1, 2, 3]
    assert [e["kind"] for e in ev] == ["hello", "ledger", "doc"]
    assert ev[1]["data"]["on_hand"] == 24
    assert [e["seq"] for e in await store.events(after=2)] == [3]


def test_make_store_memory():
    assert isinstance(make_store("x"), MemoryStore)


def test_make_store_firestore_is_lazy(monkeypatch):
    from aira.firestore_store import FirestoreStore

    monkeypatch.setattr(get_settings(), "store", "firestore")
    s = make_store("demo")
    assert isinstance(s, FirestoreStore)
    assert s.shop_id == "demo" and s._client is None  # no client built, no network touched
