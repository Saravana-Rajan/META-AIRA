"""Spend guard shared by every connection.

The web app carries a public demo key (as the original AIRA did), so limits cannot live on a
single Session: a client could reconnect to reset them. These counters are process-wide and keyed
by client IP. Cloud Run runs one instance (max-instances=1), so in-process state covers all traffic.
"""
import ipaddress
import time
from collections import defaultdict, deque

MAX_TRACKED_CLIENTS = 5000
TURNS_PER_MINUTE_PER_CLIENT = 30
TURNS_PER_HOUR_PER_CLIENT = 400
TURNS_PER_MINUTE_GLOBAL = 120
SOCKETS_PER_CLIENT = 4


class SpendGuard:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._client: dict[str, deque[float]] = defaultdict(deque)
        self._global: deque[float] = deque()
        self._sockets: dict[str, int] = defaultdict(int)

    @staticmethod
    def _trim(q: deque[float], now: float, window: float) -> None:
        while q and now - q[0] >= window:
            q.popleft()

    def allow_turn(self, client: str) -> bool:
        """Record one model turn for `client` if it is within every limit."""
        now = self._clock()
        self._trim(self._global, now, 60)
        if client not in self._client:
            if len(self._client) >= MAX_TRACKED_CLIENTS:
                self._evict_idle(now)
            if len(self._client) >= MAX_TRACKED_CLIENTS:
                return False  # table full of active callers: fail closed rather than grow without bound
        q = self._client[client]
        self._trim(q, now, 3600)
        last_minute = sum(1 for t in q if now - t < 60)
        if (
            last_minute >= TURNS_PER_MINUTE_PER_CLIENT
            or len(q) >= TURNS_PER_HOUR_PER_CLIENT
            or len(self._global) >= TURNS_PER_MINUTE_GLOBAL
        ):
            return False
        q.append(now)
        self._global.append(now)
        return True

    def _evict_idle(self, now: float) -> None:
        for key in list(self._client):
            q = self._client[key]
            self._trim(q, now, 3600)
            if not q:
                del self._client[key]

    def open_socket(self, client: str) -> bool:
        if self._sockets[client] >= SOCKETS_PER_CLIENT:
            return False
        self._sockets[client] += 1
        return True

    def close_socket(self, client: str) -> None:
        self._sockets[client] = max(0, self._sockets[client] - 1)
        if not self._sockets[client]:
            del self._sockets[client]


guard = SpendGuard()


def client_ip(headers, peer: str | None) -> str:
    """The caller's IP as seen by Cloud Run's front end.

    Google's front end APPENDS the real client address to X-Forwarded-For, so the rightmost entry is
    trustworthy and anything to its left may be forged by the caller. Only well-formed IPs are used,
    so arbitrary strings can never become guard keys.
    """
    parts = [p.strip() for p in headers.get("x-forwarded-for", "").split(",") if p.strip()]
    for candidate in (parts[-1] if parts else None, peer):
        if candidate:
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                continue
    return "unknown"
