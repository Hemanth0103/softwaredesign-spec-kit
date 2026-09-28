"""Privacy-preserving API boundaries; no request bodies or identities are retained."""

import math
import time
from ipaddress import ip_address, ip_network

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import Settings


def safe_error(status: int) -> JSONResponse:
    messages = {
        400: "Invalid request.",
        401: "Authentication required.",
        403: "Access denied.",
        404: "Not found.",
        405: "Method not allowed.",
        409: "Request conflicts with current state.",
        429: "Too many requests.",
        500: "Service unavailable.",
    }
    return JSONResponse({"error": messages.get(status, "Request failed.")}, status_code=status)


class APIMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings | None = None) -> None:
        self.app = app
        self.settings = settings
        self.window_start = time.monotonic()
        self.requests = 0
        self.responses = {str(status): 0 for status in range(1, 6)}
        self.wrapped: ASGIApp | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if self.settings is None:
            self.settings = scope["app"].state.settings
        if self.wrapped is None:
            self.wrapped = CORSMiddleware(
                self.handle,
                allow_origins=self.settings.cors_origins,
                allow_methods=["GET", "POST", "PATCH"],
                allow_headers=["Content-Type", "Authorization"],
                allow_credentials=False,
            )

        # Wrap CORS too, ensuring preflight/errors share the no-store policy.
        async def no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                if scope["path"].startswith("/api/v1/chat"):
                    headers = [
                        (k, v)
                        for k, v in message.get("headers", [])
                        if k.lower() != b"cache-control"
                    ]
                    message["headers"] = [*headers, (b"cache-control", b"no-store")]
                bucket = str(message["status"] // 100)
                if bucket in self.responses:
                    self.responses[bucket] += 1
            await send(message)

        await self.wrapped(scope, receive, no_store)

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        assert self.settings is not None
        settings = self.settings
        scope = dict(scope)
        peer = scope.get("client")
        trusted = False
        if peer:
            try:
                trusted = any(
                    ip_address(peer[0]) in ip_network(net)
                    for net in settings.trusted_proxy_networks
                )
            except ValueError:
                pass
        if trusted:
            protocols = [
                value.decode("latin1")
                for key, value in scope["headers"]
                if key.lower() == b"x-forwarded-proto"
            ]
            if len(protocols) == 1 and protocols[0] in {"http", "https"}:
                scope["scheme"] = protocols[0]
        # Never propagate untrusted forwarding data to downstream handlers.
        scope["headers"] = [
            (key, value)
            for key, value in scope["headers"]
            if key.lower()
            not in {b"forwarded", b"x-forwarded-for", b"x-forwarded-host", b"x-forwarded-proto"}
        ]
        path = scope["path"]
        if path.startswith("/api/") and path != "/api/health":
            if settings.require_https and scope["scheme"] != "https":
                await safe_error(400)(scope, receive, send)
                return
            now = time.monotonic()
            if now - self.window_start >= settings.rate_limit_window_seconds:
                self.window_start, self.requests = now, 0
            if self.requests >= settings.rate_limit_requests:
                response = safe_error(429)
                response.headers["Retry-After"] = str(
                    max(
                        1, math.ceil(settings.rate_limit_window_seconds - (now - self.window_start))
                    )
                )
                await response(scope, receive, send)
                return
            self.requests += 1
        started = False
        finished = False

        async def track_send(message: Message) -> None:
            nonlocal started, finished
            if message["type"] == "http.response.start":
                started = True
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                finished = True
            await send(message)

        try:
            await self.app(scope, receive, track_send)
        except Exception:
            # Do not re-raise into server logging or include exception values.
            if not started:
                await safe_error(500)(scope, receive, send)
            elif not finished:
                await send({"type": "http.response.body", "body": b"", "more_body": False})
            # Never attempt a second response after headers have been sent.


def configure_middleware(app: FastAPI, settings: Settings | None = None) -> None:
    """Configure before startup; main's lifespan supplies settings when omitted."""

    async def http_error(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, HTTPException)
        return safe_error(exc.status_code)

    async def validation_error(request: Request, exc: Exception) -> JSONResponse:
        return safe_error(400)

    app.add_exception_handler(HTTPException, http_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_middleware(APIMiddleware, settings=settings)
