"""Shared-token authentication as a pure ASGI middleware (covers HTTP, mounts and WebSocket)."""
from __future__ import annotations

import json
import secrets
from urllib.parse import parse_qs

from starlette.types import ASGIApp, Receive, Scope, Send

TOKEN_HEADER = "x-skyeye-token"
WS_UNAUTHORIZED = 4401


def _presented_token(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name.decode("latin-1").lower() == TOKEN_HEADER:
            return value.decode("latin-1")
    query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
    values = query.get("token")
    return values[0] if values else None


class TokenAuthMiddleware:
    """Require the shared token on every route except ``GET /``."""

    def __init__(self, app: ASGIApp, token: str | None) -> None:
        self.app = app
        self._token = token

    def _authorized(self, scope: Scope) -> bool:
        presented = _presented_token(scope)
        if not self._token or presented is None:
            return False
        return secrets.compare_digest(presented.encode(), self._token.encode())

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if kind == "http" and scope["method"] == "GET" and scope["path"] == "/":
            await self.app(scope, receive, send)
            return
        if self._authorized(scope):
            await self.app(scope, receive, send)
            return
        if kind == "websocket":
            await receive()  # websocket.connect
            await send({"type": "websocket.close", "code": WS_UNAUTHORIZED})
            return
        body = json.dumps({"api": 1, "error": "unauthorized"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
