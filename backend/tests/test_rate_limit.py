"""Per-session spend caps: the app key is public, so one phone cannot loop the models."""
from aira import session as session_mod
from tests.test_server import make_client, recv_until


def test_turns_per_minute_are_capped(monkeypatch):
    monkeypatch.setattr(session_mod, "MAX_TURNS_PER_MINUTE", 2)
    tc, _ = make_client()
    with tc, tc.websocket_connect("/ws") as ws:
        ws.send_json({"type": "hello", "lang": "en"})
        recv_until(ws, "ready")
        for text in ("one", "two"):
            ws.send_json({"type": "text", "text": text})
            recv_until(ws, "say")
        ws.send_json({"type": "text", "text": "three"})
        err, _ = recv_until(ws, "error")
        assert err["code"] == "rate_limited"


def test_long_text_is_truncated():
    s = "x" * 5000
    assert len(s.strip()[: session_mod.MAX_TEXT_CHARS]) == 500
