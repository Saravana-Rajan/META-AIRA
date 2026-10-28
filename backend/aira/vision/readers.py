"""Shared helper: ask a vision model about one frame and get strict JSON back.

Reader A = Meta Model API with the "read" model. Reader B = the independent second reader
(`reader2_client()` with the "verify" model), so the two reads do not fail the same way.
"""
import asyncio
import base64
import json
from dataclasses import dataclass
from typing import Any

from aira.config import get_settings
from aira.meta_client import meta_client, reader2_client
from aira.tools.base import FrameData

READ_TIMEOUT_S = 40.0


@dataclass
class ReadResult:
    ok: bool
    data: dict | None = None
    text: str = ""
    error: str | None = None
    model: str = ""


def data_url(frame: FrameData) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(frame.jpeg).decode("ascii")


def _effort(reader: str) -> str:
    s = get_settings()
    # Open-weights endpoints (e.g. Muse Spark Open on Baseten) always reason and expect at least "low".
    if reader == "B" and s.reader2_base_url and s.reasoning_effort == "minimal":
        return "low"
    return s.reasoning_effort


def _client_and_model(reader: str) -> tuple[Any, str]:
    s = get_settings()
    if reader == "A":
        return meta_client(), s.model_for("read")
    return reader2_client(), s.model_for("verify")


async def ask_image(
    reader: str,
    frame: FrameData,
    prompt: str,
    *,
    schema_name: str | None = None,
    schema: dict | None = None,
    timeout: float = READ_TIMEOUT_S,
) -> ReadResult:
    """One vision call. With a schema the answer is parsed JSON (strict json_schema); else plain text.

    Never raises: API errors, timeouts and malformed JSON come back as ReadResult(ok=False).
    """
    client, model = _client_and_model(reader)
    kwargs: dict[str, Any] = {
        "model": model,
        # Images are accepted in user messages only.
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url(frame)}},
                ],
            }
        ],
        "reasoning_effort": _effort(reader),
    }
    if schema is not None:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": schema_name or "reading", "schema": schema, "strict": True},
        }
    try:
        resp = await asyncio.wait_for(client.chat.completions.create(**kwargs), timeout)
    except TimeoutError:
        return ReadResult(ok=False, error="timeout", model=model)
    except Exception as e:  # noqa: BLE001 - any API failure becomes a structured failure
        return ReadResult(ok=False, error=f"{type(e).__name__}: {e}", model=model)
    try:
        text = resp.choices[0].message.content or ""
    except (AttributeError, IndexError, TypeError):
        return ReadResult(ok=False, error="empty response", model=model)
    if schema is None:
        text = text.strip()
        return ReadResult(ok=bool(text), text=text, error=None if text else "empty answer", model=model)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return ReadResult(ok=False, text=text, error="malformed json", model=model)
    if not isinstance(data, dict):
        return ReadResult(ok=False, text=text, error="malformed json", model=model)
    return ReadResult(ok=True, data=data, text=text, model=model)


async def read_both(
    frame: FrameData,
    *,
    prompt_a: str,
    schema_a: tuple[str, dict],
    prompt_b: str,
    schema_b: tuple[str, dict],
    timeout: float = READ_TIMEOUT_S,
) -> tuple[ReadResult, ReadResult]:
    """Run reader A and reader B concurrently on the same frame."""
    a, b = await asyncio.gather(
        ask_image("A", frame, prompt_a, schema_name=schema_a[0], schema=schema_a[1], timeout=timeout),
        ask_image("B", frame, prompt_b, schema_name=schema_b[0], schema=schema_b[1], timeout=timeout),
    )
    return a, b
