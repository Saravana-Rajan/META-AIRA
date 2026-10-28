"""In-process pub/sub for the owner dashboard, plus shared server state (frames, last count, sessions)."""

import asyncio
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from typing import Any


class EventBus:
    """Fan-out of live events to every subscriber. Slow subscribers drop events instead of blocking."""

    def __init__(self, maxsize: int = 256):
        self._subs: set[asyncio.Queue] = set()
        self._seq = 0
        self._maxsize = maxsize

    def publish(self, kind: str, data: dict) -> dict:
        self._seq += 1
        event = {"seq": self._seq, "kind": kind, "data": data, "ts": time.time()}
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass
        return event

    def queue(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._maxsize)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    async def subscribe(self) -> AsyncIterator[dict]:
        q = self.queue()
        try:
            while True:
                yield await q.get()
        finally:
            self.unsubscribe(q)

    @property
    def subscribers(self) -> int:
        return len(self._subs)


# Module-level default bus for code that just wants publish()/subscribe().
bus = EventBus()


def publish(kind: str, data: dict) -> dict:
    return bus.publish(kind, data)


def subscribe() -> AsyncIterator[dict]:
    return bus.subscribe()


class Hub:
    """State shared by all sessions and the dashboard routes."""

    def __init__(self, bus_: EventBus | None = None, max_frames: int = 20):
        self.bus = bus_ or bus
        self.frames: OrderedDict[str, bytes] = OrderedDict()
        self.max_frames = max_frames
        self.last_count: dict[str, Any] | None = None
        self.sessions: set[Any] = set()

    def publish(self, kind: str, data: dict) -> dict:
        return self.bus.publish(kind, data)

    def subscribe(self) -> AsyncIterator[dict]:
        return self.bus.subscribe()

    def put_frame(self, frame_id: str, jpeg: bytes) -> None:
        self.frames[frame_id] = jpeg
        self.frames.move_to_end(frame_id)
        while len(self.frames) > self.max_frames:
            self.frames.popitem(last=False)
