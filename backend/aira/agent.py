"""Muse Spark tool-calling agent (Chat Completions, PLAN 8.1) with a post-turn safety guard (PLAN 11.3)."""
import asyncio
import json
import logging
import random
from typing import Any

import openai

from aira import safety
from aira.config import Settings, get_settings
from aira.contracts import CueMsg
from aira.prompt import system_prompt
from aira.tools.base import Tool, ToolContext, err

log = logging.getLogger(__name__)

MAX_ITERATIONS = 6
MAX_HISTORY_TURNS = 20
SAFETY_RESULT_WINDOW = 40  # "this or recent turns" of tool results

FALLBACK = {
    "en": "Sorry, I cannot think right now. Please try again in a moment.",
    "ta": "மன்னிக்கவும், இப்போது பதில் சொல்ல முடியவில்லை. கொஞ்சம் கழித்து மீண்டும் சொல்லுங்கள்.",
    "hi": "माफ़ कीजिए, अभी जवाब नहीं दे पा रही हूँ। थोड़ी देर बाद फिर से कहिए।",
}
TOO_MANY_STEPS = {
    "en": "Sorry, that took too many steps. Please say it again, one thing at a time.",
    "ta": "மன்னிக்கவும், இது முடியவில்லை. ஒவ்வொன்றாக மீண்டும் சொல்லுங்கள்.",
    "hi": "माफ़ कीजिए, यह नहीं हो पाया। एक-एक करके फिर से कहिए।",
}


def _retryable(e: Exception) -> bool:
    if isinstance(e, (openai.RateLimitError, openai.APIConnectionError, openai.APITimeoutError)):
        return True
    return isinstance(e, openai.APIStatusError) and e.status_code >= 500


def _retry_after(e: Exception) -> float | None:
    resp = getattr(e, "response", None)
    headers = getattr(resp, "headers", None)
    if not headers:
        return None
    try:
        return float(headers.get("retry-after"))
    except (TypeError, ValueError):
        return None


class Agent:
    def __init__(
        self,
        ctx: ToolContext,
        tools: list[Tool] | None,
        store: Any,
        *,
        client: Any = None,
        settings: Settings | None = None,
    ):
        self.ctx = ctx
        self.store = store
        self.settings = settings or get_settings()
        if tools is None:
            from aira.registry import ALL_TOOLS  # lazy: tool modules import heavy deps

            tools = ALL_TOOLS
        self.tools: dict[str, Tool] = {t.name: t for t in tools}
        self.history: list[list[dict]] = []  # one list of messages per turn
        self.backoff_s = 1.0
        self._catalog: list[dict] | None = None
        self._client = client
        self._mock = None
        if self.settings.mock and client is None:
            from aira.mock_agent import MockAgent

            self._mock = MockAgent(ctx, list(self.tools.values()), store)

    # ---- public -------------------------------------------------------------------------------------
    async def turn(self, user_text: str) -> str:
        if self._mock is not None:
            return await self._mock.turn(user_text)

        catalog = await self._products()
        current: list[dict] = [{"role": "user", "content": user_text}]
        self.history.append(current)
        del self.history[:-MAX_HISTORY_TURNS]

        text = await self._loop(current, catalog)
        violations = safety.check(text, self.ctx.results[-SAFETY_RESULT_WINDOW:], catalog)
        if violations:
            log.warning("safety violations: %s", violations)
            await self._send_cue("warn")
            current.append({
                "role": "user",
                "content": "[SYSTEM NOTE] Correction needed: " + "; ".join(violations)
                + ". Give the corrected reply only, using numbers from tool results or PRODUCTS.",
            })
            text = await self._loop(current, catalog)
        return text

    def reset(self) -> None:
        self.history.clear()

    # ---- internals ----------------------------------------------------------------------------------
    @property
    def client(self) -> Any:
        if self._client is None:
            from aira.meta_client import meta_client

            self._client = meta_client()
        return self._client

    async def _products(self) -> list[dict]:
        if self._catalog is None:
            try:
                self._catalog = list(await self.store.products()) if self.store is not None else []
            except Exception:
                log.exception("could not load products for the prompt")
                return []
        return self._catalog

    def _messages(self, catalog: list[dict]) -> list[dict]:
        msgs: list[dict] = [{"role": "system", "content": system_prompt(catalog, self.ctx.lang)}]
        for t in self.history:
            msgs.extend(t)
        return msgs

    async def _create(self, messages: list[dict]) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.settings.model_for("agent"),
            "messages": messages,
            "reasoning_effort": self.settings.reasoning_effort,
            "prompt_cache_key": f"aira-agent-{self.ctx.shop_id}-{self.ctx.lang}",
        }
        if self.tools:
            kwargs["tools"] = [t.schema() for t in self.tools.values()]
            kwargs["tool_choice"] = "auto"
        for attempt in range(2):
            try:
                return await self.client.chat.completions.create(**kwargs)
            except Exception as e:
                if attempt == 0 and _retryable(e):
                    delay = _retry_after(e)
                    delay = min(delay if delay is not None else self.backoff_s * (1 + random.random()), 8.0)
                    log.warning("model call failed (%s); retrying in %.1fs", type(e).__name__, delay)
                    await asyncio.sleep(delay)
                    continue
                log.exception("model call failed")
                return None
        return None

    async def _loop(self, current: list[dict], catalog: list[dict]) -> str:
        lang = self.ctx.lang if self.ctx.lang in FALLBACK else "en"
        for _ in range(MAX_ITERATIONS):
            resp = await self._create(self._messages(catalog))
            if resp is None or not getattr(resp, "choices", None):
                text = FALLBACK[lang]
                current.append({"role": "assistant", "content": text})
                return text
            msg = resp.choices[0].message
            calls = list(getattr(msg, "tool_calls", None) or [])
            if not calls:
                text = (msg.content or "").strip()
                current.append({"role": "assistant", "content": text})
                return text
            current.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"},
                    }
                    for c in calls
                ],
            })
            for c in calls:
                result = await self._run_tool(c)
                current.append({
                    "role": "tool",
                    "tool_call_id": c.id,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                })
        text = TOO_MANY_STEPS[lang]
        current.append({"role": "assistant", "content": text})
        return text

    async def _run_tool(self, call: Any) -> dict:
        name = call.function.name
        tool = self.tools.get(name)
        if tool is None:
            result = err(f"unknown tool: {name}")
        else:
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError as e:
                args, result = None, err(f"arguments are not valid JSON: {e.msg}")
            else:
                if not isinstance(args, dict):
                    args, result = None, err("arguments must be a JSON object")
            if args is not None:
                self.ctx.call_id = call.id
                try:
                    result = await tool.handler(self.ctx, **args)
                except TypeError as e:
                    log.warning("bad arguments for %s: %s", name, e)
                    result = err(f"bad arguments for {name}: {e}")
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.exception("tool %s failed", name)
                    result = err(f"{name} failed: {type(e).__name__}")
                if not isinstance(result, dict):
                    result = err(f"{name} returned no result")
        self.ctx.results.append(result)
        return result

    async def _send_cue(self, cue: str) -> None:
        try:
            await self.ctx.send(CueMsg(cue=cue))
        except Exception:
            log.debug("could not send cue", exc_info=True)
