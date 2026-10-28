"""FastAPI app: phone WebSocket, dashboard REST + SSE, health."""

import asyncio
import hmac
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, get_args

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse

from aira import contracts as c
from aira.config import Role, Settings, get_settings
from aira.events import Hub
from aira.session import AgentFactory, Session, SessionClosed, TranscriberFactory

log = logging.getLogger(__name__)

STATIC_APP = Path(__file__).resolve().parent.parent / "static" / "app"
HEARTBEAT_S = 15.0
POLL_S = 1.0
RECENT = 10


def _ts(doc: dict) -> Any:
    for k in ("ts", "created_at", "updated_at", "at"):
        v = doc.get(k)
        if v is not None:
            return str(v)
    return ""


async def build_snapshot(store: Any, confirms: Any, hub: Hub) -> dict:
    products = await store.products()
    stock = await store.stock_all()
    sales = sorted(await store.query("sales"), key=_ts, reverse=True)[:RECENT]
    deliveries = sorted(await store.query("deliveries"), key=_ts, reverse=True)[:RECENT]
    pending = 0
    for s in list(hub.sessions):
        try:
            pending += len(confirms.pending(s.session_id))
        except Exception:
            pass
    return {
        "products": [{**p, "stock": stock.get(p.get("sku"), 0)} for p in products],
        "sales": sales,
        "deliveries": deliveries,
        "last_count": hub.last_count,
        "pending_confirms": pending,
        "sessions": len(hub.sessions),
        "ts": time.time(),
    }


def _sse(event: dict) -> str:
    data = json.dumps(event, default=str)
    return f"id: {event.get('seq', '')}\nevent: {event.get('kind', 'message')}\ndata: {data}\n\n"


async def sse_events(
    store: Any, hub: Hub, after: int = 0, heartbeat_s: float = HEARTBEAT_S, poll_s: float = POLL_S
) -> AsyncIterator[str]:
    """Live bus events + polled store events, with comment heartbeats so proxies keep the stream open."""
    q = hub.bus.queue()
    last_sent = time.monotonic()
    seq = after
    try:
        yield ": connected\n\n"
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), poll_s)
                yield _sse({**ev, "source": "live"})
                last_sent = time.monotonic()
            except TimeoutError:
                pass
            try:
                for ev in await store.events(seq):
                    seq = max(seq, int(ev.get("seq", seq)))
                    yield _sse({**ev, "source": "store"})
                    last_sent = time.monotonic()
            except Exception:
                log.debug("store events poll failed", exc_info=True)
            if time.monotonic() - last_sent >= heartbeat_s:
                yield ": heartbeat\n\n"
                last_sent = time.monotonic()
    finally:
        hub.bus.unsubscribe(q)


def create_app(
    *,
    settings: Settings | None = None,
    store: Any = None,
    confirms: Any = None,
    agent_factory: AgentFactory | None = None,
    transcriber_factory: TranscriberFactory | None = None,
    hub: Hub | None = None,
    frame_timeout: float | None = None,
) -> FastAPI:
    """Factory so tests can inject fakes; `app` below uses the real modules (imported lazily)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        st = app.state
        st.settings = settings or get_settings()
        st.hub = hub or Hub()
        if store is not None:
            st.store = store
        else:
            from aira.store import make_store

            st.store = make_store(st.settings.shop_id)
        if confirms is not None:
            st.confirms = confirms
        else:
            from aira.confirm import ConfirmBook

            st.confirms = ConfirmBook()
        if agent_factory is None:
            from aira.registry import ALL_TOOLS  # fail fast at boot if a tool module is broken

            st.tools = ALL_TOOLS
        st.agent_factory = agent_factory
        st.transcriber_factory = transcriber_factory
        yield
        for s in list(st.hub.sessions):
            await s.close()

    app = FastAPI(title="AIRA", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    def check_key(request: Request) -> None:
        """Fail closed: outside mock mode an app key must be configured and presented.

        The key is read from the X-Aira-Key header. Only routes a browser cannot add headers to
        (EventSource SSE and <img> frames) also accept ?key=.
        """
        s: Settings = request.app.state.settings
        if not s.app_key:
            if s.mock:
                return
            raise HTTPException(status_code=503, detail="server not configured: APP_KEY missing")
        provided = request.headers.get("x-aira-key") or ""
        if not provided and request.url.path.startswith(("/api/stream", "/api/frame/")):
            provided = request.query_params.get("key") or ""
        if not hmac.compare_digest(provided.encode(), s.app_key.encode()):
            raise HTTPException(status_code=401, detail="bad key")

    @app.get("/health")
    async def health(request: Request) -> dict:
        s: Settings = request.app.state.settings
        return {"ok": True, "mock": s.mock, "models": {r: s.model_for(r) for r in get_args(Role)}}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        st = ws.app.state

        async def ws_send(data: dict) -> None:
            await ws.send_text(json.dumps(data))

        kwargs: dict[str, Any] = {}
        if frame_timeout is not None:
            kwargs["frame_timeout"] = frame_timeout
        session = Session(
            ws_send,
            st.store,
            st.confirms,
            agent_factory=st.agent_factory,
            transcriber_factory=st.transcriber_factory,
            settings=st.settings,
            hub=st.hub,
            **kwargs,
        )
        st.hub.sessions.add(session)
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    data = json.loads(raw)
                    if not isinstance(data, dict):
                        raise ValueError("not an object")
                except ValueError:
                    await session.send(c.Error(code="bad_json"))
                    continue
                try:
                    await session.handle_raw(data)
                except SessionClosed as e:
                    await ws.close(code=e.code, reason=e.reason)
                    break
                except Exception:
                    log.exception("handle failed")
                    await session.send(c.Error(code="internal", say="Sorry, something went wrong."))
        except WebSocketDisconnect:
            pass
        finally:
            st.hub.sessions.discard(session)
            await session.close()

    @app.get("/api/snapshot", dependencies=[Depends(check_key)])
    async def snapshot(request: Request) -> dict:
        st = request.app.state
        return await build_snapshot(st.store, st.confirms, st.hub)

    @app.get("/api/events", dependencies=[Depends(check_key)])
    async def events(request: Request, after: int = 0) -> dict:
        evs = await request.app.state.store.events(after)
        last = max((int(e.get("seq", 0)) for e in evs), default=after)
        return {"events": evs, "last": last}

    @app.get("/api/stream", dependencies=[Depends(check_key)])
    async def stream(request: Request, after: int = 0) -> StreamingResponse:
        st = request.app.state
        return StreamingResponse(
            sse_events(st.store, st.hub, after),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/frame/{frame_id}", dependencies=[Depends(check_key)])
    async def frame(request: Request, frame_id: str) -> Response:
        hub_: Hub = request.app.state.hub
        if frame_id == "last" and hub_.last_count and hub_.last_count.get("frame_id"):
            frame_id = hub_.last_count["frame_id"]
        jpeg = hub_.frames.get(frame_id)
        if jpeg is None:
            raise HTTPException(status_code=404, detail="no such frame")
        return Response(content=jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    if STATIC_APP.is_dir():
        from fastapi.staticfiles import StaticFiles

        app.mount("/app", StaticFiles(directory=STATIC_APP, html=True), name="app")

    return app


app = create_app()
