"""FastAPI app exposing the engine: status, trucks, media, MJPEG stream, WebSocket, settings."""
from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.websockets import WebSocketDisconnect

from engine.api.auth import TokenAuthMiddleware
from engine.config import ApiConfig
from engine.events.bus import EventBus
from engine.events.manager import _iso, truck_to_json
from engine.pipeline import Pipeline, RuntimeSettings
from engine.storage import TruckRepository, media_dir

log = logging.getLogger(__name__)

API_VERSION = 1
VERSION = "0.1.0"
BOUNDARY = "frame"
STATUS_INTERVAL_S = 1.0
WS_POLL_S = 0.05
WS_GOING_AWAY = 1001
BUILD_HINT = "not built - run: cd dashboard && npm ci && npm run build, then reload this page"


class _LaterStaticFiles(StaticFiles):
    """StaticFiles for a folder that may only appear after startup (the dashboard
    build): every request looks again, and a missing folder is a plain 404.

    Starlette checks the folder once, on the first request, and answers 500 for
    good if it was missing then.
    """

    async def check_config(self) -> None:
        return None


@dataclass
class EngineContext:
    pipeline: Pipeline
    repo: TruckRepository
    bus: EventBus
    runtime: RuntimeSettings
    data_dir: Path
    api: ApiConfig
    token: str | None = None
    info: dict[str, Any] = field(default_factory=dict)  # served by GET /api/info
    dashboard_dir: Path | None = None  # built website; None = nothing to serve
    # Set when the engine is shutting down (Ctrl+C): long-lived responses
    # (MJPEG stream, WebSocket) end so the HTTP server can stop.
    shutdown: threading.Event = field(default_factory=threading.Event)


def _runtime_json(runtime: RuntimeSettings) -> dict[str, Any]:
    return {"api": API_VERSION, "runtime": runtime.model_dump()}


async def _status_json(ctx: EngineContext) -> dict[str, Any]:
    # status() can wait on the event manager's lock: keep it off the event loop
    status = await asyncio.to_thread(ctx.pipeline.status)
    data = asdict(status)
    for key, value in data.items():
        if isinstance(value, datetime):
            data[key] = _iso(value)
    return data


def create_app(ctx: EngineContext) -> FastAPI:
    app = FastAPI(title="sky-eye", version=VERSION)
    if ctx.api.auth.mode == "token":
        app.add_middleware(TokenAuthMiddleware, token=ctx.token)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            {"api": API_VERSION, "error": exc.detail},
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            {"api": API_VERSION, "error": "invalid request",
             "detail": jsonable_encoder(exc.errors())},
            status_code=422,
        )

    # Looked up on every request, so a build made while the engine runs is served
    # on the next page load, without a restart.
    index = ctx.dashboard_dir / "index.html" if ctx.dashboard_dir else None
    assets = ctx.dashboard_dir / "assets" if ctx.dashboard_dir else None
    banner = {"api": API_VERSION, "name": "sky-eye", "version": VERSION}

    def has_index() -> bool:
        return index is not None and index.is_file()

    if index is not None and not has_index():
        log.warning(
            "dashboard not built (%s is missing) - run: cd dashboard && npm ci && "
            "npm run build, then reload the page",
            index,
        )

    @app.get("/api")
    def api_root() -> dict[str, Any]:
        return banner

    @app.get("/api/info")
    def info() -> dict[str, Any]:
        return {"api": API_VERSION, "info": {**ctx.info, "dashboard": has_index()}}

    @app.get("/", response_model=None)
    def root() -> Any:
        if index is not None and has_index():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return {**banner, "dashboard": BUILD_HINT}

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        return {"api": API_VERSION, "status": await _status_json(ctx)}

    @app.get("/api/trucks")
    def list_trucks(
        start: Annotated[datetime | None, Query(alias="from")] = None,
        end: Annotated[datetime | None, Query(alias="to")] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        recs = ctx.repo.list_trucks(start=start, end=end, limit=limit, offset=offset)
        return {
            "api": API_VERSION,
            "trucks": [truck_to_json(r) for r in recs],
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/trucks/{truck_id}")
    def get_truck(truck_id: int) -> dict[str, Any]:
        rec = ctx.repo.get_truck(truck_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="truck not found")
        return {"api": API_VERSION, "truck": truck_to_json(rec)}

    @app.get("/api/config/runtime")
    def get_runtime() -> dict[str, Any]:
        return _runtime_json(ctx.runtime)

    @app.put("/api/config/runtime")
    async def put_runtime(request: Request) -> Any:
        # Only JSON: a cross-site page cannot send it without a CORS preflight,
        # which the engine never allows (a form or text/plain body is refused).
        media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if media_type != "application/json":
            return JSONResponse({"api": API_VERSION, "error": "expected application/json"}, 415)
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return JSONResponse({"api": API_VERSION, "error": "expected a JSON object"}, 422)
        try:
            candidate = RuntimeSettings.model_validate({**ctx.runtime.model_dump(), **body})
        except ValidationError as exc:
            return JSONResponse(
                {"api": API_VERSION, "error": "invalid runtime settings",
                 "detail": json.loads(exc.json(include_url=False, include_context=False))},
                422,
            )
        for name in RuntimeSettings.model_fields:
            setattr(ctx.runtime, name, getattr(candidate, name))  # mutate the shared instance
        return _runtime_json(ctx.runtime)

    @app.get("/stream.mjpg")
    async def stream(request: Request) -> StreamingResponse:
        period = 1.0 / max(1, ctx.api.stream_fps)

        async def frames():
            last: bytes | None = None
            while not ctx.shutdown.is_set() and not await request.is_disconnected():
                jpeg = ctx.pipeline.latest_jpeg()
                if jpeg is not None and jpeg is not last:
                    last = jpeg
                    yield (
                        f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                        f"Content-Length: {len(jpeg)}\r\n\r\n"
                    ).encode() + jpeg + b"\r\n"
                await asyncio.sleep(period)

        return StreamingResponse(
            frames(), media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}"
        )

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        q = ctx.bus.subscribe()
        loop = asyncio.get_running_loop()
        next_status = 0.0
        try:
            while not ctx.shutdown.is_set():
                if loop.time() >= next_status:
                    next_status = loop.time() + STATUS_INTERVAL_S
                    await ws.send_json(
                        {"api": API_VERSION, "type": "status", "status": await _status_json(ctx)}
                    )
                try:
                    msg = q.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(WS_POLL_S)
                    continue
                await ws.send_json(msg)
            await ws.close(code=WS_GOING_AWAY)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            ctx.bus.unsubscribe(q)

    if assets is not None:
        app.mount("/assets", _LaterStaticFiles(directory=assets, check_dir=False), name="assets")
    app.mount("/media", StaticFiles(directory=media_dir(ctx.data_dir)), name="media")
    return app
