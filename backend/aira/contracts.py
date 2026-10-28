"""WebSocket messages between the phone app and the backend. Mirrored in frontend/app/src/contracts.ts."""
from typing import Literal

from pydantic import BaseModel

FramePurpose = Literal["ask", "cash", "count", "invoice"]
Cue = Literal["ack", "thinking", "done", "warn", "match", "mismatch", "unsure"]


# ---- phone -> server -------------------------------------------------------
class Hello(BaseModel):
    type: Literal["hello"] = "hello"
    lang: str = "en"
    key: str = ""


class Audio(BaseModel):
    """80 ms of mono PCM16 little-endian at 24 kHz, base64."""

    type: Literal["audio"] = "audio"
    pcm24: str


class Frame(BaseModel):
    type: Literal["frame"] = "frame"
    id: str
    jpeg: str  # base64, no data: prefix
    w: int
    h: int
    purpose: FramePurpose = "ask"
    request_id: str | None = None


class Text(BaseModel):
    """Typed input (accessibility fallback and tests)."""

    type: Literal["text"] = "text"
    text: str


class ConfirmAnswer(BaseModel):
    type: Literal["confirm"] = "confirm"
    id: str
    answer: Literal["yes", "no"]


class Stop(BaseModel):
    type: Literal["stop"] = "stop"


# ---- server -> phone -------------------------------------------------------
class Ready(BaseModel):
    type: Literal["ready"] = "ready"
    session_id: str
    mock: bool


class Transcript(BaseModel):
    type: Literal["transcript"] = "transcript"
    role: Literal["user", "aira"]
    text: str
    final: bool = True


class Say(BaseModel):
    """Text for the phone to speak with on-device TTS (Meta has no TTS model)."""

    type: Literal["say"] = "say"
    text: str
    lang: str = "en"


class RequestFrame(BaseModel):
    type: Literal["request_frame"] = "request_frame"
    purpose: FramePurpose
    request_id: str


class Box(BaseModel):
    left: int
    top: int
    right: int
    bottom: int
    score: float | None = None


class Count(BaseModel):
    type: Literal["count"] = "count"
    sku: str
    counted: int
    expected: int | None = None
    boxes: list[Box] = []
    frame_id: str | None = None


class Confirm(BaseModel):
    type: Literal["confirm"] = "confirm"
    id: str
    kind: Literal["sale", "payment", "delivery", "stock", "generic"]
    prompt: str


class State(BaseModel):
    type: Literal["state"] = "state"
    entity: str
    id: str
    state: str
    summary: str = ""


class CueMsg(BaseModel):
    type: Literal["cue"] = "cue"
    cue: Cue


class Error(BaseModel):
    type: Literal["error"] = "error"
    code: str
    say: str = ""


INBOUND = {m.model_fields["type"].default: m for m in (Hello, Audio, Frame, Text, ConfirmAnswer, Stop)}


def parse_inbound(data: dict) -> BaseModel:
    model = INBOUND.get(data.get("type", ""))
    if model is None:
        raise ValueError(f"unknown message type: {data.get('type')!r}")
    return model.model_validate(data)
