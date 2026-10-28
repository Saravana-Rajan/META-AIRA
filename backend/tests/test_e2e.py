"""End-to-end demo over the real WebSocket with every real module wired together (mock models)."""
import base64

import pytest
from fastapi.testclient import TestClient

from aira.config import Settings
from aira.main import create_app
from aira.store import MemoryStore

JPEG = base64.b64encode(b"\xff\xd8\xff\xe0fake-jpeg\xff\xd9").decode()


def recv_until(ws, kind: str, limit: int = 40) -> tuple[dict, list[dict]]:
    seen = []
    for _ in range(limit):
        msg = ws.receive_json()
        seen.append(msg)
        if msg["type"] == kind:
            return msg, seen
    raise AssertionError(f"no {kind!r} in {seen}")


def say(ws, text: str) -> str:
    ws.send_json({"type": "text", "text": text})
    msg, _ = recv_until(ws, "say")
    return msg["text"]


def answer_frame_request(ws, purpose: str) -> list[dict]:
    req, seen = recv_until(ws, "request_frame")
    assert req["purpose"] == purpose
    ws.send_json(
        {"type": "frame", "id": f"f-{purpose}", "jpeg": JPEG, "w": 768, "h": 576,
         "purpose": purpose, "request_id": req["request_id"]}
    )
    return seen


@pytest.fixture
def app_and_store():
    store = MemoryStore("e2e")
    app = create_app(settings=Settings(aira_mock=True, model_api_key="", app_key=""), store=store)
    return app, store


async def test_full_demo_sale_and_delivery(app_and_store):
    app, store = app_and_store
    start_balls = await store.stock("TB-01")
    with TestClient(app) as tc, tc.websocket_connect("/ws") as ws:
        ws.send_json({"type": "hello", "lang": "en"})
        ready, _ = recv_until(ws, "ready")
        assert ready["mock"] is True

        assert "60" in say(ws, "price of tennis ball")

        reply = say(ws, "sell 2 tennis balls")
        assert "120" in reply  # total computed in code: 2 x 60

        # Cash payment: the server asks the phone for a frame, two (mock) readers agree on ₹500.
        ws.send_json({"type": "text", "text": "pay cash"})
        answer_frame_request(ws, "cash")
        confirm, _ = recv_until(ws, "confirm")
        assert confirm["kind"] in ("payment", "sale")
        reply, _ = recv_until(ws, "say")
        assert "380" in reply["text"]  # change 500 - 120, computed in code

        say(ws, "yes")
        assert await store.stock("TB-01") == start_balls - 2
        say(ws, "yes")  # a second yes must not sell twice
        assert await store.stock("TB-01") == start_balls - 2

        # Delivery: SAM 3.1 (mock) counts 10 balls against 12 expected.
        ws.send_json({"type": "text", "text": "check delivery of 12 tennis balls"})
        answer_frame_request(ws, "count")
        count, _ = recv_until(ws, "count")
        assert count["counted"] == 10 and count["expected"] == 12 and count["boxes"]
        recv_until(ws, "say")
        say(ws, "yes")
        assert await store.stock("TB-01") == start_balls - 2 + 10

        snap = tc.get("/api/snapshot").json()
        assert snap["last_count"]["counted"] == 10
        assert any(s.get("state") == "SALE_COMPLETED" for s in snap["sales"])
        assert tc.get(f"/api/frame/{snap['last_count']['frame_id']}").status_code == 200
