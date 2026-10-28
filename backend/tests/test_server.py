"""Server tests with fakes only: no sibling modules (store, agent, voice, confirm) are imported."""

import asyncio
import base64

import pytest
from fastapi.testclient import TestClient

from aira import contracts as c
from aira.config import Settings
from aira.events import EventBus, Hub
from aira.main import create_app, sse_events

JPEG = b"\xff\xd8\xff\xe0fakejpeg\xff\xd9"


class FakeStore:
    def __init__(self):
        self._events = [
            {"seq": 1, "kind": "sale", "data": {"id": "s1"}, "ts": 1.0},
            {"seq": 2, "kind": "ledger", "data": {"sku": "bat"}, "ts": 2.0},
        ]

    async def products(self):
        return [
            {
                "sku": "bat",
                "name": "Cricket bat",
                "price": 1200,
                "aliases": {"en": ["bat"], "ta": ["mattai"]},
            },
            {"sku": "ball", "name": "Tennis ball", "price": 50, "aliases": {"en": [], "hi": ["gend"]}},
        ]

    async def stock_all(self):
        return {"bat": 4, "ball": 30}

    async def query(self, collection, **equals):
        if collection == "sales":
            return [{"id": "s1", "total": 1200, "ts": "2026-10-28T10:00:00"}]
        if collection == "deliveries":
            return [{"id": "d1", "sku": "ball", "counted": 12, "ts": "2026-10-28T09:00:00"}]
        return []

    async def events(self, after=0):
        return [e for e in self._events if e["seq"] > after]


class FakeConfirms:
    def __init__(self):
        self.resolved = []
        self.rejected = []

    async def resolve(self, session_id, confirm_id, answer):
        self.resolved.append((session_id, confirm_id, answer))
        if answer == "yes":
            return {"status": "ok", "say_en": "Sale saved. Total 1200 rupees."}
        return {"status": "rejected"}

    def reject_all(self, session_id):
        self.rejected.append(session_id)

    def pending(self, session_id):
        return [{"id": "p1"}]


class FakeAgent:
    def __init__(self, ctx):
        self.ctx = ctx

    async def turn(self, text):
        if text == "count":
            frame = await self.ctx.request_frame("count")
            if frame is None:
                return "No frame."
            await self.ctx.send(
                c.Count(sku="ball", counted=12, expected=10, boxes=[c.Box(left=1, top=2, right=3, bottom=4)])
            )
            return f"I counted 12. Frame {frame.id} has {len(frame.jpeg)} bytes."
        if text == "slow":
            await asyncio.sleep(30)
            return "never"
        if text == "boom":
            raise RuntimeError("kaboom")
        return f"You said {text}."


class FakeTranscriber:
    instances: list["FakeTranscriber"] = []

    def __init__(self, on_final, on_partial=None, keywords=(), languages=None):
        self.on_final, self.on_partial = on_final, on_partial
        self.keywords, self.languages = list(keywords), languages
        self.pushed: list[bytes] = []
        self.started = self.closed = False
        FakeTranscriber.instances.append(self)

    async def start(self):
        self.started = True

    async def push(self, pcm):
        self.pushed.append(pcm)
        if len(self.pushed) == 1:
            await self.on_partial("hel")
            await self.on_final("hello there")

    async def close(self):
        self.closed = True


def make_client(app_key: str = "", frame_timeout: float = 2.0):
    confirms = FakeConfirms()
    app = create_app(
        settings=Settings(aira_mock=True, app_key=app_key, model_api_key=""),
        store=FakeStore(),
        confirms=confirms,
        agent_factory=FakeAgent,
        transcriber_factory=FakeTranscriber,
        hub=Hub(EventBus()),
        frame_timeout=frame_timeout,
    )
    return TestClient(app), confirms


@pytest.fixture
def client():
    tc, confirms = make_client()
    with tc:
        tc.confirms = confirms
        yield tc


def hello(ws, **kw):
    ws.send_json({"type": "hello", "lang": "en", **kw})
    msg = ws.receive_json()
    assert msg["type"] == "ready", msg
    return msg


def recv_until(ws, type_, limit=10):
    seen = []
    for _ in range(limit):
        m = ws.receive_json()
        seen.append(m)
        if m["type"] == type_:
            return m, seen
    raise AssertionError(f"no {type_} in {seen}")


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["mock"] is True
    assert set(body["models"]) == {"agent", "read", "verify", "sam", "asr"}


def test_hello_ready_and_transcriber(client):
    FakeTranscriber.instances.clear()
    with client.websocket_connect("/ws") as ws:
        ready = hello(ws)
        assert ready["mock"] is True and ready["session_id"]
        t = FakeTranscriber.instances[-1]
        assert t.started
        assert {"Cricket bat", "bat", "mattai", "gend"} <= set(t.keywords)
        assert t.languages == ["Tamil", "English", "Hindi"]
        # audio -> push -> partial + final -> agent turn
        ws.send_json({"type": "audio", "pcm24": base64.b64encode(b"\x00\x01" * 10).decode()})
        partial = ws.receive_json()
        assert partial == {"type": "transcript", "role": "user", "text": "hel", "final": False}
        say, _ = recv_until(ws, "say")
        assert say["text"] == "You said hello there."
        assert t.pushed == [b"\x00\x01" * 10]
    assert t.closed


def test_text_turn(client):
    with client.websocket_connect("/ws") as ws:
        hello(ws)
        ws.send_json({"type": "text", "text": "price of bat"})
        assert ws.receive_json() == {
            "type": "transcript",
            "role": "user",
            "text": "price of bat",
            "final": True,
        }
        assert ws.receive_json() == {"type": "cue", "cue": "thinking"}
        assert ws.receive_json() == {
            "type": "transcript",
            "role": "aira",
            "text": "You said price of bat.",
            "final": True,
        }
        assert ws.receive_json() == {"type": "say", "text": "You said price of bat.", "lang": "en"}


def test_turn_error_is_spoken(client):
    with client.websocket_connect("/ws") as ws:
        hello(ws)
        ws.send_json({"type": "text", "text": "boom"})
        err, _ = recv_until(ws, "error")
        assert err["say"] == "Sorry, something went wrong. Please say that again."


def test_bad_messages(client):
    with client.websocket_connect("/ws") as ws:
        hello(ws)
        ws.send_text("not json")
        assert ws.receive_json()["code"] == "bad_json"
        ws.send_json({"type": "nope"})
        assert ws.receive_json()["code"] == "bad_message"
        ws.send_json({"type": "text"})
        assert ws.receive_json()["code"] == "bad_message"
        ws.send_json({"type": "text", "text": "still alive"})
        say, _ = recv_until(ws, "say")
        assert say["text"] == "You said still alive."


def test_frame_round_trip_and_snapshot(client):
    with client.websocket_connect("/ws") as ws:
        hello(ws)
        ws.send_json({"type": "text", "text": "count"})
        req, _ = recv_until(ws, "request_frame")
        assert req["purpose"] == "count"
        ws.send_json(
            {
                "type": "frame",
                "id": "f1",
                "jpeg": base64.b64encode(JPEG).decode(),
                "w": 640,
                "h": 480,
                "purpose": "count",
                "request_id": req["request_id"],
            }
        )
        count, _ = recv_until(ws, "count")
        assert count["counted"] == 12 and count["frame_id"] == "f1"
        say, _ = recv_until(ws, "say")
        assert say["text"] == f"I counted 12. Frame f1 has {len(JPEG)} bytes."

        r = client.get("/api/frame/f1")
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content == JPEG
        assert client.get("/api/frame/last").content == JPEG
        assert client.get("/api/frame/zzz").status_code == 404

        snap = client.get("/api/snapshot").json()
        assert [p["stock"] for p in snap["products"]] == [4, 30]
        assert snap["sales"][0]["id"] == "s1" and snap["deliveries"][0]["id"] == "d1"
        assert snap["last_count"]["frame_id"] == "f1" and snap["last_count"]["boxes"][0]["left"] == 1
        assert snap["pending_confirms"] == 1


def test_frame_matched_by_purpose_without_request_id(client):
    with client.websocket_connect("/ws") as ws:
        hello(ws)
        ws.send_json({"type": "text", "text": "count"})
        recv_until(ws, "request_frame")
        frame = {"type": "frame", "id": "f2", "jpeg": base64.b64encode(JPEG).decode(), "w": 1, "h": 1}
        ws.send_json({**frame, "purpose": "count"})
        say, _ = recv_until(ws, "say")
        assert "Frame f2" in say["text"]


def test_frame_timeout():
    tc, _ = make_client(frame_timeout=0.2)
    with tc, tc.websocket_connect("/ws") as ws:
        hello(ws)
        ws.send_json({"type": "text", "text": "count"})
        say, _ = recv_until(ws, "say")
        assert say["text"] == "No frame."


def test_confirm_answer(client):
    with client.websocket_connect("/ws") as ws:
        ready = hello(ws)
        ws.send_json({"type": "confirm", "id": "cf1", "answer": "yes"})
        t = ws.receive_json()
        assert t == {
            "type": "transcript",
            "role": "aira",
            "text": "Sale saved. Total 1200 rupees.",
            "final": True,
        }
        assert ws.receive_json()["type"] == "say"
        ws.send_json({"type": "confirm", "id": "cf2", "answer": "no"})
        assert ws.receive_json()["text"] == "Okay, cancelled."
        assert client.confirms.resolved == [
            (ready["session_id"], "cf1", "yes"),
            (ready["session_id"], "cf2", "no"),
        ]


def test_stop_cancels_turn(client):
    with client.websocket_connect("/ws") as ws:
        ready = hello(ws)
        ws.send_json({"type": "text", "text": "slow"})
        recv_until(ws, "cue")  # thinking
        ws.send_json({"type": "text", "text": "queued"})  # waits behind the slow turn, then dropped by stop
        ws.send_json({"type": "stop"})
        assert ws.receive_json() == {"type": "cue", "cue": "ack"}
        assert client.confirms.rejected == [ready["session_id"]]
        ws.send_json({"type": "text", "text": "after"})
        say, seen = recv_until(ws, "say")
        assert say["text"] == "You said after."
        assert all("queued" not in m.get("text", "") for m in seen)


def test_app_key():
    tc, _ = make_client(app_key="sekret")
    with tc:
        with tc.websocket_connect("/ws") as ws:
            ws.send_json({"type": "hello", "key": "wrong"})
            assert ws.receive_json()["code"] == "unauthorized"
        with tc.websocket_connect("/ws") as ws:
            hello(ws, key="sekret")
        assert tc.get("/api/snapshot").status_code == 401
        assert tc.get("/api/snapshot?key=sekret").status_code == 200
        assert tc.get("/health").status_code == 200


def test_events_poll(client):
    body = client.get("/api/events?after=1").json()
    assert [e["seq"] for e in body["events"]] == [2] and body["last"] == 2


async def test_sse_stream_and_bus():
    hub = Hub(EventBus())
    gen = sse_events(FakeStore(), hub, after=0, heartbeat_s=0.05, poll_s=0.01)
    assert (await anext(gen)).startswith(": connected")
    first = await anext(gen)
    assert "event: sale" in first and '"source": "store"' in first
    await anext(gen)  # ledger
    hub.publish("say", {"text": "hi"})
    live = await anext(gen)
    assert "event: say" in live and '"source": "live"' in live
    assert (await anext(gen)) == ": heartbeat\n\n"
    await gen.aclose()
    assert hub.bus.subscribers == 0


async def test_bus_subscribe():
    bus = EventBus()
    sub = bus.subscribe()
    task = asyncio.create_task(anext(sub))
    await asyncio.sleep(0)
    bus.publish("count", {"n": 1})
    ev = await asyncio.wait_for(task, 1)
    assert ev["kind"] == "count" and ev["data"] == {"n": 1} and ev["seq"] == 1
    await sub.aclose()
