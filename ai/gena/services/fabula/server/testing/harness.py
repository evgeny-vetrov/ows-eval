"""A server in the test's own event loop: the application with its lifespan, an HTTP
client over ASGI and a reader of event streams."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from ai.gena.services.fabula.engine.testing.kit import STANDARD_CAPABILITIES, STANDARD_EVENTS
from ai.gena.services.fabula.server.app import create_app
from ai.gena.services.fabula.server.composition import Components, build
from ai.gena.services.fabula.server.sandbox.clock import SandboxClock
from ai.gena.services.fabula.server.settings import ServerSettings


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        merged[key] = _merge(merged[key], value) if isinstance(value, dict) and isinstance(merged.get(key), dict) else value
    return merged


def make_settings(**overrides: Any) -> ServerSettings:
    """Dev authentication (every user an admin, agents resolvers), the engine's test
    catalog, no background jobs; `overrides` are merged section by section."""
    base = {
        "auth": {"mode": "dev", "dev_actor": "user:alice", "grants": {"user:*": ["admin"], "agent:*": ["resolver"], "service:*": ["viewer"]}},
        "catalog": {
            "capabilities": [c.model_dump(mode="json") for c in STANDARD_CAPABILITIES],
            "events": [e.model_dump(mode="json") for e in STANDARD_EVENTS],
        },
        "background": {"reconcile_seconds": 0},
        "stream": {"heartbeat_seconds": 0.05},
    }
    return ServerSettings.model_validate(_merge(base, overrides))


class Server:
    def __init__(self, components: Components, client: httpx.AsyncClient, app):
        self.components = components
        self.client = client
        self.app = app

    @property
    def sandbox(self):
        return self.components.sandbox

    async def call(self, method: str, url: str, *, expect: int | None = None, actor: str | None = None, **kwargs: Any) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}) or {})
        if actor is not None:
            headers["X-Fabula-Actor"] = actor
        response = await self.client.request(method, url, headers=headers, **kwargs)
        if expect is not None:
            assert response.status_code == expect, (method, url, response.status_code, response.text)
        return response

    async def get(self, url: str, **kwargs: Any) -> Any:
        return (await self.call("GET", url, expect=kwargs.pop("expect", 200), **kwargs)).json()

    async def post(self, url: str, body: Any = None, **kwargs: Any) -> Any:
        return (await self.call("POST", url, json=body, expect=kwargs.pop("expect", 200), **kwargs)).json()

    async def settle(self) -> None:
        result = await self.sandbox.settle()
        assert result.settled and not result.dead_letters, result

    def stream(self, url: str, headers: dict[str, str] | None = None, spec_version: str = "2.3") -> "EventStream":
        return EventStream(self.app, url, headers or {}, spec_version)


@asynccontextmanager
async def running(settings: ServerSettings | None = None, clock: SandboxClock | None = None) -> AsyncIterator[Server]:
    components = build(settings or make_settings(), clock=clock)
    app = create_app(components=components)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            yield Server(components, client, app)


@dataclass
class Frame:
    id: str | None
    event: str | None
    data: Any


class EventStream:
    """Reads a server-sent event stream straight from the ASGI app (an HTTP client
    over ASGI waits for the whole body, and a stream has no end).

    Under ASGI `spec_version` 2.4 writes after the client left are dropped silently,
    as uvicorn does, and Starlette no longer watches for the disconnect itself."""

    def __init__(self, app, url: str, headers: dict[str, str], spec_version: str = "2.3"):
        path, _, query = url.partition("?")
        headers = {"host": "localhost", **headers}
        self.scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": spec_version},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query.encode(),
            "root_path": "",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": ("127.0.0.1", 40000),
            "server": ("fabula.test", 80),
        }
        self.app = app
        self.status: int | None = None
        self.headers: dict[str, str] = {}
        self.frames: list[Frame] = []
        self.comments = 0
        # What an EventSource would send as Last-Event-ID on a reconnect.
        self.last_id: str | None = None
        self.body = ""
        self._buffer = ""
        self._changed = asyncio.Event()
        self._disconnect = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def __aenter__(self) -> "EventStream":
        requested = False

        async def receive():
            nonlocal requested
            if not requested:
                requested = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await self._disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if self._disconnect.is_set():
                return
            if message["type"] == "http.response.start":
                self.status = message["status"]
                self.headers = {k.decode().lower(): v.decode() for k, v in message["headers"]}
            elif message["type"] == "http.response.body":
                chunk = message.get("body", b"").decode()
                self.body += chunk
                self._buffer += chunk
                while "\n\n" in self._buffer:
                    block, self._buffer = self._buffer.split("\n\n", 1)
                    self._parse(block)
            self._changed.set()

        self._task = asyncio.create_task(self.app(self.scope, receive, send))
        await self.wait(lambda: self.status is not None or self._task.done())
        return self

    def _parse(self, block: str) -> None:
        fields: dict[str, str] = {}
        for line in block.split("\n"):
            if line.startswith(":"):
                self.comments += 1
                continue
            name, _, value = line.partition(": ")
            fields[name] = value
        if "id" in fields:
            self.last_id = fields["id"]
        if "event" in fields or "data" in fields:
            self.frames.append(Frame(fields.get("id"), fields.get("event"), json.loads(fields["data"]) if "data" in fields else None))

    async def wait(self, condition, timeout: float = 5) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not condition():
            remaining = deadline - loop.time()
            assert remaining > 0, f"timed out; frames so far: {[(f.event, f.id) for f in self.frames]}"
            self._changed.clear()
            try:
                await asyncio.wait_for(self._changed.wait(), remaining)
            except asyncio.TimeoutError:
                pass

    async def events(self, count: int, timeout: float = 5) -> list[Frame]:
        await self.wait(lambda: len(self.frames) >= count, timeout)
        return self.frames[:count]

    def kinds(self) -> list[str]:
        return [frame.event for frame in self.frames]

    async def disconnect(self) -> None:
        self._disconnect.set()
        if self._task is not None:
            await asyncio.wait_for(self._task, 5)

    async def __aexit__(self, *exc) -> None:
        await self.disconnect()


def query(**params: Any) -> str:
    return urlencode(params, doseq=True)
