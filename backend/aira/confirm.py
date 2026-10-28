"""Propose -> spoken/tapped "yes" -> commit.

Nothing that changes stock or money is written until the owner says yes. A per-proposal lock makes a
spoken yes and a tapped yes arriving together commit exactly once.
"""

import asyncio
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from aira.contracts import Confirm

log = logging.getLogger(__name__)

CONFIRM_KINDS = ("sale", "payment", "delivery", "stock", "generic")
PENDING, COMMITTED, REJECTED = "pending", "committed", "rejected"


@dataclass
class Proposal:
    id: str
    session_id: str
    kind: str
    prompt: str
    facts: dict
    committer: Callable[[], Awaitable[dict]]
    expires_at: float
    state: str = PENDING
    result: dict | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ConfirmBook:
    def __init__(self, ttl_s: float = 120, clock: Callable[[], float] = time.monotonic):
        self.ttl_s = ttl_s
        self._clock = clock
        self._items: dict[str, Proposal] = {}

    def _expired(self, p: Proposal) -> bool:
        return self._clock() > p.expires_at

    async def propose(
        self,
        ctx: Any,
        *,
        kind: str,
        prompt: str,
        facts: dict,
        committer: Callable[[], Awaitable[dict]],
    ) -> dict:
        cid = f"c_{secrets.token_hex(5)}"
        while cid in self._items:
            cid = f"c_{secrets.token_hex(5)}"
        self._items[cid] = Proposal(
            id=cid,
            session_id=ctx.session_id,
            kind=kind,
            prompt=prompt,
            facts=dict(facts),
            committer=committer,
            expires_at=self._clock() + self.ttl_s,
        )
        card_kind = kind if kind in CONFIRM_KINDS else "generic"
        await ctx.send(Confirm(id=cid, kind=card_kind, prompt=prompt))
        try:  # dashboard visibility only; never block a proposal on it
            await ctx.store.put(
                "proposals",
                cid,
                {
                    "session_id": ctx.session_id,
                    "kind": kind,
                    "prompt": prompt,
                    "state": PENDING,
                    "ts": time.time(),
                },
            )
        except Exception:
            log.warning("could not record proposal %s", cid, exc_info=True)
        return {"status": "ok", **facts, "needs_confirm": True, "confirm_id": cid, "say_en": prompt}

    async def resolve(self, session_id: str, confirm_id: str, answer: str) -> dict:
        p = self._items.get(confirm_id)
        if p is None or p.session_id != session_id:
            return {"status": "error", "message": "unknown confirmation", "retryable": False}
        async with p.lock:
            if p.state == COMMITTED:
                stored = p.result or {}
                return {
                    **stored,
                    "status": "ok",
                    "already_done": True,
                    "confirm_id": p.id,
                    "say_en": "Already done. " + stored.get("say_en", ""),
                }
            if p.state == REJECTED:
                return self._rejected(p, "cancelled")
            if self._expired(p):
                p.state = REJECTED
                return self._rejected(p, "expired")
            if (answer or "").strip().lower() != "yes":
                p.state = REJECTED
                return self._rejected(p, "no")
            try:
                result = await p.committer()
            except Exception as e:  # the proposal stays pending so the owner can retry with yes
                log.warning("committer for %s failed", p.id, exc_info=True)
                return {
                    "status": "error",
                    "message": f"could not complete: {e}",
                    "retryable": True,
                    "confirm_id": p.id,
                    "still_pending": True,
                }
            p.state = COMMITTED
            p.result = dict(result or {})
            return {**p.result, "status": p.result.get("status", "ok"), "confirm_id": p.id}

    @staticmethod
    def _rejected(p: Proposal, reason: str) -> dict:
        say = {
            "expired": "That confirmation expired, nothing was changed. Ask again if you still want it.",
            "no": "Cancelled, nothing was changed.",
            "cancelled": "That was already cancelled, nothing was changed.",
        }[reason]
        return {"status": "ok", "rejected": True, "reason": reason, "confirm_id": p.id, "say_en": say}

    def reject_all(self, session_id: str) -> None:
        for p in self._items.values():
            if p.session_id == session_id and p.state == PENDING and not p.lock.locked():
                p.state = REJECTED

    def pending(self, session_id: str) -> list[dict]:
        now = self._clock()
        return [
            {
                "confirm_id": p.id,
                "kind": p.kind,
                "prompt": p.prompt,
                "facts": dict(p.facts),
                "expires_in_s": round(p.expires_at - now, 1),
            }
            for p in self._items.values()
            if p.session_id == session_id and p.state == PENDING and not self._expired(p)
        ]
