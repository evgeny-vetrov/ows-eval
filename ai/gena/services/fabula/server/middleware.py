"""Plain ASGI middleware (it must not buffer streams): trace ids, the access log, the
host check and compression of everything but event streams."""

import logging
import re
import time
import uuid
from fnmatch import fnmatchcase

from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ai.gena.services.fabula.server.errors import problem_response

TRACE_HEADER = "X-Trace-Id"
_TRACE = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")

access_log = logging.getLogger("ai.gena.services.fabula.server.access")


class TraceMiddleware:
    """Takes `X-Trace-Id` from the caller (or makes one), returns it on the response,
    and writes one access-log line per request with the operation and the actor.

    Not to be confused with `request_id` in bodies, which makes a mutation idempotent.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = Headers(scope=scope).get(TRACE_HEADER)
        trace_id = incoming if incoming and _TRACE.match(incoming) else uuid.uuid4().hex
        state = scope.setdefault("state", {})
        state["trace_id"] = trace_id
        started = time.perf_counter()
        status = 500

        async def send_traced(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message).append(TRACE_HEADER, trace_id)
            await send(message)

        try:
            await self.app(scope, receive, send_traced)
        finally:
            access_log.info(
                "%s %s %s %d %.1fms actor=%s trace=%s",
                scope["method"],
                scope["path"],
                state.get("operation", "-"),
                status,
                (time.perf_counter() - started) * 1000,
                state.get("actor", "-"),
                trace_id,
            )


class GZipExceptStreams:
    """Compresses responses, except event streams, which must reach the client as they
    are written whatever the Starlette version does by default."""

    def __init__(self, app: ASGIApp, minimum_size: int = 1024):
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=minimum_size)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].endswith(":stream"):
            await self.app(scope, receive, send)
        else:
            await self.gzip(scope, receive, send)


def host_name(host: str) -> str:
    """`example.org:8080` -> `example.org`, `[::1]:8080` -> `::1`."""
    if host.startswith("["):
        return host[1 : host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


class TrustedHosts:
    """Answers only requests addressed to an allowed host name. A page on another site
    that rebinds its DNS name to this server still sends its own name in `Host`."""

    def __init__(self, app: ASGIApp, patterns: tuple[str, ...]):
        self.app = app
        self.patterns = tuple(p.lower() for p in patterns)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and "*" not in self.patterns:
            name = host_name(Headers(scope=scope).get("host", "")).lower()
            if not any(fnmatchcase(name, pattern) for pattern in self.patterns):
                response = problem_response(400, "untrusted_host", f"this server does not answer to host '{name}'")
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
