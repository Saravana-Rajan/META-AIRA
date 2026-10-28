"""Muse Voice Transcribe realtime client (PLAN.md 8.2).

One Transcriber per phone session. Audio pushed by the phone is buffered and a pacer sends exactly one
80 ms chunk (3840 bytes of 24 kHz PCM16 mono) per 80 ms of wall clock, padding with silence when the
buffer runs dry, because the server closes the socket (1008) on input that is too far ahead or behind
real time. `speechComplete` events drive `on_final`.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable, Iterable
from urllib.parse import urlencode

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from aira.config import get_settings

log = logging.getLogger(__name__)

SAMPLE_RATE = 24_000
BYTES_PER_SAMPLE = 2
CHUNK_S = 0.08
CHUNK_BYTES = int(SAMPLE_RATE * CHUNK_S) * BYTES_PER_SAMPLE  # 3840
MAX_BUFFER_BYTES = SAMPLE_RATE * BYTES_PER_SAMPLE * 5  # 5 s of audio
SILENCE = bytes(CHUNK_BYTES)

RECONNECT_CODES = {1011, 1013, 1006, 1001}  # internal error / session cap, rate limited, dropped, going away
MAX_RECONNECTS = 5

Callback = Callable[[str], Awaitable[None]]


class _HandshakeRejected(Exception):
    """The server refused the handshake (error event or close before the sessionId ack)."""


class Transcriber:
    HANDSHAKE_TIMEOUT_S = 10.0
    CLOSE_TIMEOUT_S = 3.0
    SESSION_MAX_S = 55 * 60.0  # rotate before the server's 60-minute cap
    RECONNECT_BACKOFF_S = 0.5  # 1013 (rate limited) waits 4x longer

    def __init__(
        self,
        on_final: Callback,
        on_partial: Callback | None = None,
        keywords: Iterable[str] = (),
        languages: list[str] | None = None,
        *,
        url: str | None = None,
        api_key: str | None = None,
        session_id: str | None = None,
        mock: bool | None = None,
    ) -> None:
        settings = get_settings()
        self.on_final = on_final
        self.on_partial = on_partial
        self.keywords = [k for k in keywords if k][:100]
        self.languages = list(languages) if languages is not None else list(settings.asr_languages)
        self.url = url or settings.meta_asr_url
        self.api_key = api_key if api_key is not None else settings.model_api_key
        self.model = settings.model_for("asr")
        self.request_session_id = session_id
        # Explicit url/api_key means a real (or fake test) server even when settings say mock.
        self.mock = mock if mock is not None else (settings.mock and api_key is None and url is None)

        self.session_id: str | None = None
        self.stopped = False  # True after a non-retryable close (1008) or too many failed reconnects
        self._use_bare_key = False
        self._buf = bytearray()
        self._ws: ClientConnection | None = None
        self._closing = False
        self._pacer: asyncio.Task | None = None
        self._supervisor: asyncio.Task | None = None
        self._dispatcher: asyncio.Task | None = None
        self._events: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._turns: dict[str, dict] = {}  # turnId -> {"partial": str, "ended": bool}
        self._dropped_bytes = 0

    # ------------------------------------------------------------------ public API

    async def start(self) -> None:
        if self.mock:
            return
        self._ws = await self._open()
        self._dispatcher = asyncio.create_task(self._dispatch(), name="asr-dispatch")
        self._supervisor = asyncio.create_task(self._supervise(), name="asr-supervisor")
        self._pacer = asyncio.create_task(self._pace(), name="asr-pacer")

    async def push(self, pcm24: bytes) -> None:
        if self.mock or self._closing or not pcm24:
            return
        self._buf.extend(pcm24)
        overflow = len(self._buf) - MAX_BUFFER_BYTES
        if overflow > 0:
            overflow += overflow % BYTES_PER_SAMPLE  # keep sample alignment
            del self._buf[:overflow]
            self._dropped_bytes += overflow
            log.warning("ASR buffer over 5 s; dropped %d oldest bytes (%d total)",
                        overflow, self._dropped_bytes)

    async def close(self) -> None:
        if self.mock or self._closing:
            return
        self._closing = True
        if self._pacer:
            self._pacer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pacer
        ws = self._ws
        if ws is not None and not self.stopped:
            try:
                # Flush what is buffered (<= 5 s, so never too far ahead), then end the stream.
                while self._buf:
                    await ws.send(self._take_chunk())
                await ws.send(json.dumps({"type": "endStream"}))
            except ConnectionClosed:
                pass
        if self._supervisor:
            try:
                await asyncio.wait_for(asyncio.shield(self._supervisor), self.CLOSE_TIMEOUT_S)
            except TimeoutError:
                log.warning("ASR server did not close within %.1f s after endStream", self.CLOSE_TIMEOUT_S)
                self._supervisor.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._supervisor
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()
        if self._dispatcher:
            try:
                await asyncio.wait_for(self._events.join(), self.CLOSE_TIMEOUT_S)
            except TimeoutError:
                log.warning("ASR callbacks still running at close")
            self._dispatcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._dispatcher

    # ------------------------------------------------------------------ connection

    def _connect_url(self) -> str:
        if not self.request_session_id:
            return self.url
        sep = "&" if "?" in self.url else "?"
        return f"{self.url}{sep}{urlencode({'sessionId': self.request_session_id})}"

    def _handshake(self, bare: bool) -> str:
        token = self.api_key if bare else f"Bearer {self.api_key}"
        return json.dumps({
            "authorization": {"accessToken": token},
            "audioEncoding": "PCM_24KHZ",
            "model": self.model,
            "mode": "ENDPOINTING",
            "partialMode": "CUMULATIVE",
            "emitAudioProgress": False,
            "languageBias": self.languages,
            "keywords": self.keywords,
        })

    async def _open(self) -> ClientConnection:
        """Connect and handshake. If "Bearer <key>" is rejected, retry once with the bare key."""
        attempts = [True] if self._use_bare_key else [False, True]
        last: Exception | None = None
        for bare in attempts:
            ws = await connect(self._connect_url(), open_timeout=self.HANDSHAKE_TIMEOUT_S, max_size=None)
            try:
                await ws.send(self._handshake(bare))
                self.session_id = await asyncio.wait_for(self._await_ack(ws), self.HANDSHAKE_TIMEOUT_S)
            except (_HandshakeRejected, TimeoutError) as e:
                last = e
                with contextlib.suppress(Exception):
                    await ws.close()
                if isinstance(e, _HandshakeRejected) and not bare:
                    log.warning("ASR rejected 'Bearer' token (%s); retrying with the bare key", e)
                    continue
                break
            except BaseException:
                with contextlib.suppress(Exception):
                    await ws.close()
                raise
            self._use_bare_key = bare
            log.info("ASR session %s open (%s key)", self.session_id, "bare" if bare else "Bearer")
            return ws
        raise ConnectionError(f"ASR handshake failed: {last}")

    async def _await_ack(self, ws: ClientConnection) -> str:
        try:
            while True:
                raw = await ws.recv()
                if isinstance(raw, bytes):
                    continue
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                if "type" not in msg and "sessionId" in msg:
                    return str(msg["sessionId"])
                if msg.get("type") == "error":
                    raise _HandshakeRejected(msg.get("message") or msg.get("error") or json.dumps(msg))
        except ConnectionClosed as e:
            raise _HandshakeRejected(f"closed {ws.close_code} {ws.close_reason or ''}".strip()) from e

    async def _supervise(self) -> None:
        """Own the socket: run the reader, reconnect on 1011/1013 and rotate before the 60-min cap."""
        loop = asyncio.get_running_loop()
        opened_at = loop.time()
        ws = self._ws
        assert ws is not None
        reader = asyncio.create_task(self._read(ws), name="asr-reader")
        while True:
            remaining = max(0.0, opened_at + self.SESSION_MAX_S - loop.time())
            done, _ = await asyncio.wait({reader}, timeout=None if self._closing else remaining)
            if not done:  # proactive rotation before the session cap; keep the buffer
                try:
                    new_ws = await self._open()
                except Exception as e:
                    log.warning("ASR proactive reconnect failed (%s); retrying in 30 s", e)
                    opened_at += 30.0
                    continue
                self._ws = new_ws
                asyncio.create_task(self._retire(ws, reader), name="asr-retire")
                ws, opened_at = new_ws, loop.time()
                reader = asyncio.create_task(self._read(ws), name="asr-reader")
                continue
            code = reader.result()
            if self._closing:
                return
            if code not in RECONNECT_CODES:
                log.error("ASR socket closed with %s (%s); not reconnecting", code, ws.close_reason)
                self._stop()
                return
            self._ws = None  # the pacer holds audio while we reconnect
            new_ws = await self._reconnect(code)
            if new_ws is None:
                return
            ws, opened_at = new_ws, loop.time()
            self._ws = ws
            reader = asyncio.create_task(self._read(ws), name="asr-reader")

    async def _reconnect(self, code: int | None) -> ClientConnection | None:
        for attempt in range(1, MAX_RECONNECTS + 1):
            delay = self.RECONNECT_BACKOFF_S * (2 ** (attempt - 1)) * (4 if code == 1013 else 1)
            log.warning("ASR socket closed with %s; reconnect %d in %.2f s", code, attempt, delay)
            await asyncio.sleep(delay)
            if self._closing:
                return None
            try:
                return await self._open()
            except Exception as e:
                log.warning("ASR reconnect %d failed: %s", attempt, e)
        log.error("ASR gave up after %d reconnect attempts", MAX_RECONNECTS)
        self._stop()
        return None

    def _stop(self) -> None:
        self.stopped = True
        self._ws = None

    async def _retire(self, ws: ClientConnection, reader: asyncio.Task) -> None:
        """End a rotated-out session, still delivering any finals it emits."""
        with contextlib.suppress(Exception):
            await ws.send(json.dumps({"type": "endStream"}))
        try:
            await asyncio.wait_for(reader, self.CLOSE_TIMEOUT_S)
        except (TimeoutError, Exception):
            reader.cancel()
        with contextlib.suppress(Exception):
            await ws.close()

    # ------------------------------------------------------------------ audio pacing

    def _take_chunk(self) -> bytes:
        n = min(CHUNK_BYTES, len(self._buf))
        if n == 0:
            return SILENCE
        chunk = bytes(self._buf[:n])
        del self._buf[:n]
        return chunk if n == CHUNK_BYTES else chunk + bytes(CHUNK_BYTES - n)

    async def _pace(self) -> None:
        """Send one 80 ms chunk per 80 ms against an absolute clock so drift never accumulates."""
        loop = asyncio.get_running_loop()
        started = loop.time()
        n = 0
        while not self._closing:
            n += 1
            target = started + n * CHUNK_S
            delay = target - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            elif delay < -1.0:  # event loop stalled; resync instead of bursting a second of audio
                log.warning("ASR pacer %.2f s behind; resyncing", -delay)
                started, n = loop.time(), 0
            if self.stopped:
                return
            ws = self._ws
            if ws is None:  # reconnecting: hold the buffer (capped in push)
                continue
            chunk = self._take_chunk()
            try:
                await ws.send(chunk)
            except ConnectionClosed:
                pass  # the supervisor sees the close and reconnects

    # ------------------------------------------------------------------ events

    async def _read(self, ws: ClientConnection) -> int | None:
        try:
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                try:
                    msg = json.loads(raw)
                except ValueError:
                    log.debug("ASR non-JSON frame ignored")
                    continue
                if isinstance(msg, dict):
                    self._handle(msg)
        except ConnectionClosed:
            pass
        return ws.close_code

    def _handle(self, msg: dict) -> None:
        kind = msg.get("type")
        turn = str(msg.get("turnId", ""))
        if kind == "transcript":
            text = (msg.get("transcript") or "").strip()
            state = self._turns.setdefault(turn, {"partial": "", "ended": False})
            state["partial"] = text  # cumulative: replace, don't append
            if text and self.on_partial is not None:
                self._events.put_nowait(("partial", text))
        elif kind == "speechStart":
            self._turns.setdefault(turn, {"partial": "", "ended": False})
        elif kind == "speechEnd":
            self._turns.setdefault(turn, {"partial": "", "ended": False})["ended"] = True
        elif kind == "speechComplete":
            self._turns.pop(turn, None)
            text = (msg.get("transcript") or "").strip()
            if text:
                self._events.put_nowait(("final", text))
        elif kind == "error":
            log.error("ASR error event: %s", msg)
        # speaker, audioProgress, the sessionId ack and unknown types are ignored.

    async def _dispatch(self) -> None:
        """Run callbacks in order without blocking the socket reader."""
        while True:
            kind, text = await self._events.get()
            try:
                if kind == "final":
                    await self.on_final(text)
                elif self.on_partial is not None:
                    await self.on_partial(text)
            except Exception:
                log.exception("ASR %s callback failed", kind)
            finally:
                self._events.task_done()
