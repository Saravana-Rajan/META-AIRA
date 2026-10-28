"""Transcriber against a local fake Muse Voice Transcribe server (no real network)."""
import asyncio
import json

import pytest
from websockets.asyncio.server import serve

from aira import voice
from aira.voice import CHUNK_BYTES, MAX_BUFFER_BYTES, Transcriber


class FakeASR:
    """Records handshakes and audio per connection; `script(conn_index, ws, state)` drives each session."""

    def __init__(self, *, reject_bearer=False, close_first_with=None, events_after_chunks=None):
        self.reject_bearer = reject_bearer
        self.close_first_with = close_first_with
        self.events_after_chunks = events_after_chunks  # (n_chunks, [events])
        self.handshakes: list[dict] = []
        self.paths: list[str] = []
        self.chunks: list[list[bytes]] = []
        self.chunk_times: list[list[float]] = []
        self.text_frames: list[dict] = []
        self.accepted = 0

    async def handler(self, ws):
        self.paths.append(ws.request.path)
        first = json.loads(await asyncio.wait_for(ws.recv(), 10))
        self.handshakes.append(first)
        token = first["authorization"]["accessToken"]
        if self.reject_bearer and token.startswith("Bearer "):
            await ws.send(json.dumps({"type": "error", "message": "invalid_api_key"}))
            await ws.close(1008, "unauthorized")
            return
        idx = self.accepted
        self.accepted += 1
        await ws.send(json.dumps({"sessionId": f"s{idx}"}))
        chunks: list[bytes] = []
        times: list[float] = []
        self.chunks.append(chunks)
        self.chunk_times.append(times)
        if idx == 0 and self.close_first_with:
            await asyncio.sleep(0.1)
            await ws.close(self.close_first_with, "test")
            return
        sent = False
        async for msg in ws:
            if isinstance(msg, bytes):
                chunks.append(msg)
                times.append(asyncio.get_running_loop().time())
                if self.events_after_chunks and not sent and len(chunks) >= self.events_after_chunks[0]:
                    sent = True
                    for ev in self.events_after_chunks[1]:
                        await ws.send(json.dumps(ev))
            else:
                data = json.loads(msg)
                self.text_frames.append(data)
                if data.get("type") == "endStream":
                    await ws.send(json.dumps({"type": "speechComplete", "turnId": "tz", "transcript": " "}))
                    await ws.close(1000)
                    return


async def run_server(fake):
    server = await serve(fake.handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, f"ws://127.0.0.1:{port}/v1/asr/realtime"


TURN = [
    {"type": "speechStart", "turnId": "t1"},
    {"type": "transcript", "turnId": "t1", "transcript": "two", "final": False},
    {"type": "transcript", "turnId": "t1", "transcript": "two bats", "final": False},
    {"type": "speaker", "turnId": "t1"},
    {"type": "mysteryEvent"},
    {"type": "speechEnd", "turnId": "t1"},
    {"type": "speechComplete", "turnId": "t1", "transcript": "  Two bats, please.  "},
    {"type": "speechComplete", "turnId": "t2", "transcript": "   "},
]


def make(url, finals, partials=None, **kw):
    async def on_final(t):
        finals.append(t)

    async def on_partial(t):
        partials.append(t)

    return Transcriber(on_final, on_partial if partials is not None else None,
                       keywords=kw.pop("keywords", ["bat", "ball"]), languages=["Tamil", "English"],
                       url=url, api_key="LLM_key", **kw)


async def test_handshake_events_and_pacing():
    fake = FakeASR(events_after_chunks=(3, TURN))
    server, url = await run_server(fake)
    finals, partials = [], []
    t = make(url, finals, partials, keywords=[f"k{i}" for i in range(150)])
    await t.start()
    assert t.session_id == "s0"
    await t.push(b"\x01\x00" * 4800)  # 0.2 s of audio
    await asyncio.sleep(0.5)
    await t.close()
    server.close()

    hs = fake.handshakes[0]
    assert hs["authorization"] == {"accessToken": "Bearer LLM_key"}
    assert hs["audioEncoding"] == "PCM_24KHZ"
    assert hs["mode"] == "ENDPOINTING" and hs["partialMode"] == "CUMULATIVE"
    assert hs["emitAudioProgress"] is False
    assert hs["model"] == "muse-voice-transcribe-1.0"
    assert hs["languageBias"] == ["Tamil", "English"]
    assert len(hs["keywords"]) == 100

    chunks = fake.chunks[0]
    assert all(len(c) == CHUNK_BYTES for c in chunks)
    # 0.5 s at 80 ms per chunk ~ 6 chunks; never bursts ahead of real time.
    assert 4 <= len(chunks) <= 9, len(chunks)
    times = fake.chunk_times[0]
    span = times[min(5, len(times) - 1)] - times[0]
    assert span >= 0.08 * (min(5, len(times) - 1)) - 0.05
    assert finals == ["Two bats, please."]
    assert partials == ["two", "two bats"]
    assert fake.text_frames[-1] == {"type": "endStream"}


async def test_silence_padding():
    fake = FakeASR()
    server, url = await run_server(fake)
    t = make(url, [])
    await t.start()
    await t.push(b"\x07" * 1000)
    await asyncio.sleep(0.3)
    await t.close()
    server.close()
    chunks = fake.chunks[0]
    assert len(chunks) >= 2
    assert all(len(c) == CHUNK_BYTES for c in chunks)
    assert chunks[0] == b"\x07" * 1000 + bytes(CHUNK_BYTES - 1000)
    assert all(c == bytes(CHUNK_BYTES) for c in chunks[1:])


async def test_bare_key_retry():
    fake = FakeASR(reject_bearer=True)
    server, url = await run_server(fake)
    t = make(url, [])
    await t.start()
    await asyncio.sleep(0.1)
    await t.close()
    server.close()
    tokens = [h["authorization"]["accessToken"] for h in fake.handshakes]
    assert tokens == ["Bearer LLM_key", "LLM_key"]
    assert fake.accepted == 1


async def test_reconnect_on_1011_keeps_streaming(monkeypatch):
    monkeypatch.setattr(Transcriber, "RECONNECT_BACKOFF_S", 0.01)
    fake = FakeASR(close_first_with=1011)
    server, url = await run_server(fake)
    t = make(url, [])
    await t.start()
    await asyncio.sleep(0.5)
    assert fake.accepted == 2
    assert not t.stopped
    await t.close()
    server.close()
    assert len(fake.chunks[1]) >= 2


async def test_proactive_rotation(monkeypatch):
    monkeypatch.setattr(Transcriber, "SESSION_MAX_S", 0.25)
    fake = FakeASR()
    server, url = await run_server(fake)
    t = make(url, [])
    await t.start()
    await asyncio.sleep(0.45)
    await t.close()
    server.close()
    assert fake.accepted >= 2
    assert {"type": "endStream"} in fake.text_frames  # old session ended cleanly
    assert len(fake.chunks[1]) >= 1


async def test_1008_stops_without_retry(monkeypatch):
    monkeypatch.setattr(Transcriber, "RECONNECT_BACKOFF_S", 0.01)
    fake = FakeASR(close_first_with=1008)
    server, url = await run_server(fake)
    t = make(url, [])
    await t.start()
    await asyncio.sleep(0.4)
    assert t.stopped
    assert fake.accepted == 1
    await t.close()
    server.close()


async def test_session_id_query_param():
    fake = FakeASR()
    server, url = await run_server(fake)
    t = make(url, [], session_id="abc")
    await t.start()
    await t.close()
    server.close()
    assert fake.paths[0].endswith("?sessionId=abc")


async def test_buffer_is_capped():
    t = make("ws://unused", [])
    await t.push(b"\x01\x00" * (MAX_BUFFER_BYTES // 2 + 1000))
    assert len(t._buf) == MAX_BUFFER_BYTES


async def test_mock_mode_is_noop(monkeypatch):
    finals = []

    async def on_final(text):
        finals.append(text)

    monkeypatch.setattr(voice, "get_settings", lambda: _MockSettings())
    t = Transcriber(on_final)
    assert t.mock
    await t.start()
    await t.push(b"\x00" * 10)
    await t.close()
    assert t._ws is None and not t._buf


class _MockSettings:
    mock = True
    asr_languages = ["English"]
    meta_asr_url = "wss://example.invalid"
    model_api_key = ""

    def model_for(self, role):
        return "muse-voice-transcribe-1.0"


@pytest.fixture(autouse=True)
def _fast_close(monkeypatch):
    monkeypatch.setattr(Transcriber, "CLOSE_TIMEOUT_S", 1.0)
