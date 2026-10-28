"""One phone connection: hello -> ready, audio/text -> agent turns, frames, confirmations, stop."""

import asyncio
import base64
import binascii
import contextlib
import hmac
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel

from aira import contracts as c
from aira.config import Settings
from aira.events import Hub
from aira.limits import guard
from aira.tools.base import FrameData, ToolContext

log = logging.getLogger(__name__)

SORRY = "Sorry, something went wrong. Please say that again."
RATE_LIMITED = "I need a short pause. Please try again in a minute."

MAX_TEXT_CHARS = 500
MAX_TURNS_PER_MINUTE = 20
MAX_TURNS_PER_SESSION = 300
FRAME_TIMEOUT_S = 8.0

WsSend = Callable[[dict], Awaitable[None]]
AgentFactory = Callable[[ToolContext], Any]  # -> object with `async turn(text) -> str`
TranscriberFactory = Callable[..., Any]  # (on_final, on_partial, keywords, languages) -> Transcriber


class SessionClosed(Exception):
    """Raised by Session.handle when the connection must be closed (e.g. bad app key)."""

    def __init__(self, code: int = 1008, reason: str = ""):
        super().__init__(reason)
        self.code = code
        self.reason = reason


def default_agent_factory(store: Any) -> AgentFactory:
    def make(ctx: ToolContext) -> Any:
        from aira.agent import Agent
        from aira.registry import ALL_TOOLS

        return Agent(ctx, ALL_TOOLS, store)

    return make


def default_transcriber_factory(on_final, on_partial=None, keywords=(), languages=None) -> Any:
    from aira.voice import Transcriber

    return Transcriber(on_final, on_partial, keywords=keywords, languages=languages)


class Session:
    def __init__(
        self,
        ws_send: WsSend,
        store: Any,
        confirms: Any,
        agent_factory: AgentFactory | None = None,
        transcriber_factory: TranscriberFactory | None = None,
        settings: Settings | None = None,
        hub: Hub | None = None,
        frame_timeout: float = FRAME_TIMEOUT_S,
        client: str = "local",
    ):
        self.client = client  # caller identity (IP) for the shared spend guard
        if settings is None:
            from aira.config import get_settings

            settings = get_settings()
        self.session_id = uuid.uuid4().hex[:12]
        self.settings = settings
        self.store = store
        self.confirms = confirms
        self.hub = hub or Hub()
        self.frame_timeout = frame_timeout
        self._ws_send = ws_send
        self._agent_factory = agent_factory or default_agent_factory(store)
        self._transcriber_factory = transcriber_factory or default_transcriber_factory
        self._send_lock = asyncio.Lock()

        self.authed = False  # until a valid hello; see _on_hello
        self.lang = "en"
        self.ctx = ToolContext(
            session_id=self.session_id,
            shop_id=settings.shop_id,
            store=store,
            confirms=confirms,
            request_frame=self.request_frame,
            send=self.send,
        )
        self.agent: Any = None
        self.transcriber: Any = None

        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._turn_times: list[float] = []
        self._turns_total = 0
        self._worker: asyncio.Task | None = None
        self._turn: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        # request_id -> (purpose, future)
        self._pending_frames: dict[str, tuple[str, asyncio.Future]] = {}
        self.last_frames: dict[str, FrameData] = {}
        self.last_count_frame_id: str | None = None
        self.closed = False

    # ---- outbound -----------------------------------------------------------
    async def send(self, msg: BaseModel) -> None:
        if isinstance(msg, c.Count):
            if msg.frame_id is None and self.last_count_frame_id:
                msg = msg.model_copy(update={"frame_id": self.last_count_frame_id})
            self.hub.last_count = {**msg.model_dump(), "session_id": self.session_id}
        data = msg.model_dump()
        if data.get("type") in ("transcript", "say", "count", "confirm", "state", "error"):
            self.hub.publish(data["type"], {**data, "session_id": self.session_id})
        if self.closed:
            return
        async with self._send_lock:
            try:
                await self._ws_send(data)
            except Exception:  # socket gone; the receive loop will notice and close us
                log.debug("send failed", exc_info=True)

    async def _say(self, text: str) -> None:
        await self.send(c.Transcript(role="aira", text=text))
        await self.send(c.Say(text=text, lang=self.lang))

    # ---- inbound ------------------------------------------------------------
    async def handle_raw(self, data: dict) -> None:
        try:
            msg = c.parse_inbound(data)
        except Exception as e:  # unknown type or validation error
            await self.send(c.Error(code="bad_message", say=""))
            log.info("bad message: %s", e)
            return
        await self.handle(msg)

    async def handle(self, msg: BaseModel) -> None:
        if isinstance(msg, c.Hello):
            await self._on_hello(msg)
            return
        if not self.authed:
            await self.send(c.Error(code="unauthorized", say="This app is not allowed to connect."))
            raise SessionClosed(1008, "unauthorized")
        if isinstance(msg, c.Audio):
            await self._on_audio(msg)
        elif isinstance(msg, c.Text):
            await self.on_user_text(msg.text)
        elif isinstance(msg, c.Frame):
            await self._on_frame(msg)
        elif isinstance(msg, c.ConfirmAnswer):
            self._spawn(self._on_confirm(msg))
        elif isinstance(msg, c.Stop):
            await self._on_stop()

    async def _on_hello(self, msg: c.Hello) -> None:
        key = self.settings.app_key
        # Fail closed: outside mock mode a key must be configured and match.
        allowed = hmac.compare_digest(msg.key.encode(), key.encode()) if key else self.settings.mock
        if not allowed:
            await self.send(c.Error(code="unauthorized", say="This app is not allowed to connect."))
            raise SessionClosed(1008, "unauthorized")
        self.authed = True
        self.lang = msg.lang or "en"
        self.ctx.lang = self.lang
        await self.send(c.Ready(session_id=self.session_id, mock=self.settings.mock))
        self._ensure_agent()
        if self.transcriber is None:
            await self._start_transcriber()

    async def _keywords(self) -> list[str]:
        words: list[str] = []
        try:
            for p in await self.store.products():
                if p.get("name"):
                    words.append(p["name"])
                for aliases in (p.get("aliases") or {}).values():
                    words.extend(a for a in aliases or [] if a)
        except Exception:
            log.warning("could not load keywords", exc_info=True)
        return list(dict.fromkeys(words))

    async def _start_transcriber(self) -> None:
        async def on_partial(text: str) -> None:
            if text:
                await self.send(c.Transcript(role="user", text=text, final=False))

        try:
            self.transcriber = self._transcriber_factory(
                on_final=self.on_user_text,
                on_partial=on_partial,
                keywords=await self._keywords(),
                languages=self.settings.asr_languages,
            )
            await self.transcriber.start()
        except Exception:
            log.exception("transcriber start failed")
            await self.send(c.Error(code="asr_unavailable", say="I cannot hear right now. Please type."))

    async def _on_audio(self, msg: c.Audio) -> None:
        if self.transcriber is None:
            return
        try:
            pcm = base64.b64decode(msg.pcm24, validate=True)
        except (binascii.Error, ValueError):
            await self.send(c.Error(code="bad_audio"))
            return
        try:
            await self.transcriber.push(pcm)
        except Exception:
            log.exception("transcriber push failed")

    # ---- turns --------------------------------------------------------------
    def _ensure_agent(self) -> None:
        if self.agent is None:
            self.agent = self._agent_factory(self.ctx)
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._work())

    async def on_user_text(self, text: str) -> None:
        text = (text or "").strip()[:MAX_TEXT_CHARS]
        if not text or self.closed:
            return
        # The app key is public (built into the web app), so each session's model spend is capped.
        now = time.monotonic()
        self._turn_times = [t for t in self._turn_times if now - t < 60]
        over_session = (
            len(self._turn_times) >= MAX_TURNS_PER_MINUTE or self._turns_total >= MAX_TURNS_PER_SESSION
        )
        if over_session or not guard.allow_turn(self.client):  # guard: shared across reconnects
            await self.send(c.Error(code="rate_limited", say=RATE_LIMITED))
            return
        self._turn_times.append(now)
        self._turns_total += 1
        self._ensure_agent()
        await self._queue.put(text)

    async def _work(self) -> None:
        while not self.closed:
            text = await self._queue.get()
            self._turn = asyncio.create_task(self._run_turn(text))
            try:
                await self._turn
            except asyncio.CancelledError:
                task = asyncio.current_task()
                if self.closed or (task is not None and task.cancelling()):
                    raise
                # turn cancelled by Stop: keep serving
            finally:
                self._turn = None

    async def _run_turn(self, text: str) -> None:
        await self.send(c.Transcript(role="user", text=text, final=True))
        await self.send(c.CueMsg(cue="thinking"))
        try:
            reply = await self.agent.turn(text)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("turn failed")
            await self.send(c.Error(code=f"turn_failed:{type(e).__name__}", say=SORRY))
            return
        if reply:
            await self._say(reply)

    # ---- frames -------------------------------------------------------------
    async def request_frame(self, purpose: str) -> FrameData | None:
        request_id = uuid.uuid4().hex[:12]
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_frames[request_id] = (purpose, fut)
        try:
            await self.send(c.RequestFrame(purpose=purpose, request_id=request_id))
            return await asyncio.wait_for(fut, self.frame_timeout)
        except TimeoutError:
            return None
        finally:
            self._pending_frames.pop(request_id, None)

    async def _on_frame(self, msg: c.Frame) -> None:
        try:
            jpeg = base64.b64decode(msg.jpeg, validate=True)
        except (binascii.Error, ValueError):
            await self.send(c.Error(code="bad_frame"))
            return
        frame = FrameData(id=msg.id, jpeg=jpeg, w=msg.w, h=msg.h)
        self.last_frames[msg.purpose] = frame
        if msg.purpose == "count":
            self.last_count_frame_id = msg.id
            self.hub.put_frame(msg.id, jpeg)
        target = None
        if msg.request_id and msg.request_id in self._pending_frames:
            target = self._pending_frames[msg.request_id][1]
        else:
            for purpose, fut in reversed(list(self._pending_frames.values())):
                if purpose == msg.purpose and not fut.done():
                    target = fut
                    break
        if target is not None and not target.done():
            target.set_result(frame)

    # ---- confirm / stop -----------------------------------------------------
    async def _on_confirm(self, msg: c.ConfirmAnswer) -> None:
        try:
            result = await self.confirms.resolve(self.session_id, msg.id, msg.answer)
        except Exception:
            log.exception("confirm resolve failed")
            await self.send(c.Error(code="confirm_failed", say=SORRY))
            return
        result = result or {}
        say = result.get("say_en")
        if not say:
            if result.get("status") == "ok":
                say = "Done." if msg.answer == "yes" else "Okay, cancelled."
            elif result.get("status") == "rejected" or msg.answer == "no":
                say = "Okay, cancelled."
            else:
                say = result.get("message") or "I could not do that."
        await self._say(say)

    async def _on_stop(self) -> None:
        while not self._queue.empty():
            self._queue.get_nowait()
        if self._turn is not None and not self._turn.done():
            self._turn.cancel()
        for _, fut in self._pending_frames.values():
            if not fut.done():
                fut.set_result(None)
        try:
            self.confirms.reject_all(self.session_id)
        except Exception:
            log.exception("reject_all failed")
        await self.send(c.CueMsg(cue="ack"))

    # ---- lifecycle ----------------------------------------------------------
    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.transcriber is not None:
            with contextlib.suppress(Exception):
                await self.transcriber.close()
        tasks = [t for t in (self._turn, self._worker, *self._tasks) if t is not None and not t.done()]
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(BaseException):
                await t
        for _, fut in self._pending_frames.values():
            if not fut.done():
                fut.cancel()
        with contextlib.suppress(Exception):
            self.confirms.reject_all(self.session_id)
