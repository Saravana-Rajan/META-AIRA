"""Tool interface shared by every shop tool and the agent loop."""
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel

from aira.contracts import FramePurpose


@dataclass
class FrameData:
    id: str
    jpeg: bytes
    w: int
    h: int


class FrameProvider(Protocol):
    async def __call__(self, purpose: FramePurpose) -> FrameData | None: ...


@dataclass
class ToolContext:
    """Everything a tool may touch. Built once per session; call_id changes per tool call."""

    session_id: str
    shop_id: str
    store: Any  # aira.store.Store
    confirms: Any  # aira.confirm.ConfirmBook
    request_frame: FrameProvider
    send: Callable[[BaseModel], Awaitable[None]]
    call_id: str = ""
    lang: str = "en"
    results: list[dict] = field(default_factory=list)  # every tool result this session (safety guard input)

    def idem_key(self) -> str:
        """Same session + same tool call -> same key, so a retried call rewrites the same documents."""
        return hashlib.sha256(f"{self.session_id}:{self.call_id}".encode()).hexdigest()[:24]


Handler = Callable[..., Awaitable[dict]]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON schema (object)
    handler: Handler  # async (ctx: ToolContext, **args) -> dict
    needs_confirm: bool = False

    def schema(self) -> dict:
        """Chat Completions function-tool definition."""
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


def ok(say_en: str = "", **facts: Any) -> dict:
    return {"status": "ok", **facts, "say_en": say_en}


def err(message: str, retryable: bool = False, **facts: Any) -> dict:
    return {"status": "error", "message": message, "retryable": retryable, **facts}


def no_results(say_en: str, **facts: Any) -> dict:
    return {"status": "no_results", **facts, "say_en": say_en}


def obj(properties: dict, required: list[str] | None = None) -> dict:
    """JSON schema helper for tool parameters."""
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }
