"""Two-reader cash: agreement, disagreement, unsupported notes. Offline."""
import json
from types import SimpleNamespace

import pytest

from aira.config import Settings
from aira.tools.base import FrameData
from aira.vision import cash, readers
from aira.vision.readers import ReadResult

FRAME = FrameData(id="f1", jpeg=b"\xff\xd8jpeg", w=640, h=480)
S = Settings(aira_mock=False, model_api_key="k", reader2_base_url="", reader2_model="")


def A(groups, overlap=False, other=False):
    return ReadResult(ok=True, data={"groups": groups, "overlap": overlap, "other_currency": other})


def B(values, overlap=False, other=False):
    return ReadResult(ok=True, data={"values": values, "overlap": overlap, "other_currency": other})


def g(d, n):
    return {"denomination": d, "count": n}


def test_agree_total_in_code():
    r = cash.judge(A([g(500, 1), g(100, 2), g(10, 1)]), B([100, 500, 10, 100]))
    assert r.agreed and r.total == 710 and r.notes == {500: 1, 100: 2, 10: 1} and r.hint is None


def test_disagree_counts():
    r = cash.judge(A([g(100, 2)]), B([100, 100, 100]))
    assert not r.agreed and r.total is None and r.notes == {} and r.hint == cash.HINT_SPREAD


def test_unsupported_2000_note():
    r = cash.judge(A([g(2000, 1)]), B([2000]))
    assert not r.agreed and r.hint == cash.HINT_UNSUPPORTED


def test_other_currency_and_overlap():
    assert cash.judge(A([g(100, 1)], other=True), B([100])).hint == cash.HINT_UNSUPPORTED
    assert cash.judge(A([g(100, 1)]), B([100], overlap=True)).hint == cash.HINT_SPREAD


def test_empty_failure_malformed():
    assert not cash.judge(A([]), B([])).agreed
    assert cash.judge(ReadResult(ok=False, error="timeout"), B([100])).hint == cash.HINT_AGAIN
    bad = ReadResult(ok=True, data={"groups": [{"denomination": "100", "count": 1}]})
    assert cash.judge(bad, B([100])).hint == cash.HINT_AGAIN


def test_coins_supported():
    r = cash.judge(A([g(5, 2), g(20, 1)]), B([5, 20, 5]))
    assert r.agreed and r.total == 30


class FakeCompletions:
    def __init__(self, by_model):
        self.by_model, self.calls = by_model, []

    async def create(self, **kw):
        self.calls.append(kw)
        out = self.by_model[kw["model"]]
        if isinstance(out, Exception):
            raise out
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(out)))])


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(cash, "get_settings", lambda: S)
    monkeypatch.setattr(readers, "get_settings", lambda: S)

    def install(by_model):
        fake = FakeCompletions(by_model)
        client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
        monkeypatch.setattr(readers, "meta_client", lambda: client)
        monkeypatch.setattr(readers, "reader2_client", lambda: client)
        return fake

    return install


async def test_read_cash_end_to_end(live):
    fake = live({
        S.model_for("read"): {"groups": [g(200, 1), g(50, 1)], "overlap": False, "other_currency": False},
        S.model_for("verify"): {"values": [50, 200], "overlap": False, "other_currency": False},
    })
    r = await cash.read_cash(FRAME)
    assert r.agreed and r.total == 250
    assert {c["model"] for c in fake.calls} == {S.model_for("read"), S.model_for("verify")}
    for c in fake.calls:
        assert c["response_format"]["type"] == "json_schema"
        assert c["response_format"]["json_schema"]["strict"] is True
        assert c["reasoning_effort"] == S.reasoning_effort
        msg = c["messages"][0]
        assert msg["role"] == "user"
        assert msg["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


async def test_read_cash_api_error(live):
    live({
        S.model_for("read"): RuntimeError("down"),
        S.model_for("verify"): {"values": [100], "overlap": False, "other_currency": False},
    })
    r = await cash.read_cash(FRAME)
    assert not r.agreed and r.hint == cash.HINT_AGAIN


async def test_mock(monkeypatch):
    monkeypatch.setattr(cash, "get_settings", lambda: Settings(aira_mock=True))
    r = await cash.read_cash(FRAME)
    assert r.agreed and r.total == 500 and r.notes == {500: 1}


def test_schemas_strict():
    for schema in (cash.SCHEMA_A, cash.SCHEMA_B):
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
