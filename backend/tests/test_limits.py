"""The spend guard is shared across connections, so reconnecting does not reset it."""
from aira import limits
from aira.limits import SpendGuard, client_ip


def test_per_client_minute_cap_survives_reconnect(monkeypatch):
    monkeypatch.setattr(limits, "TURNS_PER_MINUTE_PER_CLIENT", 3)
    now = [0.0]
    g = SpendGuard(clock=lambda: now[0])
    assert all(g.allow_turn("1.2.3.4") for _ in range(3))
    assert not g.allow_turn("1.2.3.4")  # a "new session" from the same IP is still capped
    assert g.allow_turn("5.6.7.8")  # other callers unaffected
    now[0] = 61
    assert g.allow_turn("1.2.3.4")  # window slides


def test_global_cap(monkeypatch):
    monkeypatch.setattr(limits, "TURNS_PER_MINUTE_GLOBAL", 2)
    g = SpendGuard(clock=lambda: 0.0)
    assert g.allow_turn("a") and g.allow_turn("b")
    assert not g.allow_turn("c")


def test_socket_cap(monkeypatch):
    monkeypatch.setattr(limits, "SOCKETS_PER_CLIENT", 2)
    g = SpendGuard()
    assert g.open_socket("x") and g.open_socket("x")
    assert not g.open_socket("x")
    g.close_socket("x")
    assert g.open_socket("x")


def test_client_ip_uses_rightmost_hop_and_rejects_junk():
    # A caller can prepend anything; Cloud Run appends the real address last.
    assert client_ip({"x-forwarded-for": "6.6.6.6, 9.9.9.9"}, "10.0.0.1") == "9.9.9.9"
    assert client_ip({"x-forwarded-for": "spoof-me"}, "10.0.0.1") == "10.0.0.1"
    assert client_ip({}, "127.0.0.1") == "127.0.0.1"
    assert client_ip({}, None) == "unknown"


def test_tracked_clients_are_bounded(monkeypatch):
    monkeypatch.setattr(limits, "MAX_TRACKED_CLIENTS", 2)
    now = [0.0]
    g = SpendGuard(clock=lambda: now[0])
    assert g.allow_turn("1.1.1.1") and g.allow_turn("2.2.2.2")
    assert not g.allow_turn("3.3.3.3")  # full of active callers
    now[0] = 3601  # both idle for an hour -> evicted
    assert g.allow_turn("3.3.3.3")
