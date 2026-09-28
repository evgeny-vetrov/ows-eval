"""`GET /v1/notifications:stream`: server-sent events for the web UI.

Every notification is `id: <boot>.<seq>`, `event: <kind>`, `data: <ManagerEvent>`.
A client that reconnects with `Last-Event-ID` gets what it missed while the server
still buffers it; otherwise it gets `event: reset` and refetches what it shows. An
actor sees only notifications about resources it may read.

The stream moves the client's `Last-Event-ID` even when it sends nothing to show: an
id-only block at the start (where a fresh subscription begins) and after notifications
it filtered out. So a reconnect never silently skips notifications.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from fnmatch import fnmatchcase
from typing import Any

from fastapi import Depends, FastAPI, Request
from starlette.responses import StreamingResponse

from ai.gena.services.fabula.manager.model.audit import ManagerEvent
from ai.gena.services.fabula.manager.model.common import Actor
from ai.gena.services.fabula.server.dependencies import stream_actor

STREAM_PATH = "/v1/notifications:stream"
RETRY_MILLISECONDS = 3000

# Permission needed to see a notification, by the prefix of its resource.
READ_PERMISSIONS = (
    ("fabula:", "fabula.read"),
    ("scenario:", "scenario.read"),
    ("migration:", "migration.read"),
    ("resolution-task:", "migration.read"),
)


def read_permission(resource: str) -> str:
    for prefix, permission in READ_PERMISSIONS:
        if resource.startswith(prefix):
            return permission
    return "audit.read"


def frame(event_id: str, kind: str, data: Any) -> str:
    return f"id: {event_id}\nevent: {kind}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False)}\n\n"


def position(event_id: str) -> str:
    """Sets the client's last event id without dispatching an event."""
    return f"id: {event_id}\n\n"


async def _disconnected(request: Request) -> None:
    """Returns when the client goes away. Starlette stops watching for that itself
    under ASGI 2.4, where a server may drop writes to a closed connection silently."""
    while True:
        message = await request.receive()
        if message["type"] == "http.disconnect":
            return


def _wanted(event: ManagerEvent, kinds: list[str], resource: str | None) -> bool:
    if kinds and not any(fnmatchcase(event.kind, pattern) for pattern in kinds):
        return False
    return resource is None or event.resource.startswith(resource)


async def _mark(request: Request) -> None:
    request.state.operation = "streamNotifications"


def add_stream_route(app: FastAPI) -> None:
    @app.get(STREAM_PATH, include_in_schema=False, response_model=None, dependencies=[Depends(_mark)])
    async def stream_notifications(request: Request, actor: Actor = Depends(stream_actor)) -> StreamingResponse:
        components = request.app.state.components
        hub, authorizer = components.hub, components.authorizer
        kinds = request.query_params.getlist("kind")
        resource = request.query_params.get("resource")
        last_event_id = request.headers.get("last-event-id") or request.query_params.get("last_event_id")
        heartbeat = components.settings.stream.heartbeat_seconds

        async def events() -> AsyncIterator[str]:
            subscription = hub.subscribe(last_event_id)
            start = None if last_event_id else hub.last_id
            gone = asyncio.ensure_future(_disconnected(request))
            waiting: asyncio.Future | None = None
            try:
                yield f"retry: {RETRY_MILLISECONDS}\n\n" + (position(start) if start else "")
                while True:
                    waiting = asyncio.ensure_future(subscription.next(heartbeat))
                    await asyncio.wait({waiting, gone}, return_when=asyncio.FIRST_COMPLETED)
                    if gone.done():
                        return
                    got = waiting.result()
                    if got is None:
                        yield ": keep-alive\n\n"
                        continue
                    lost, items = got
                    if lost:
                        before = f"{hub.boot}.{items[0].seq - 1}" if items else hub.last_id
                        yield frame(before, "reset", {"reason": "notifications were missed: refetch what is shown"})
                    sent = None
                    for notification in items:
                        event = notification.event
                        if not _wanted(event, kinds, resource):
                            continue
                        if not await authorizer.allowed(actor, read_permission(event.resource), event.resource):
                            continue
                        yield frame(notification.id, event.kind, event.model_dump(mode="json"))
                        sent = notification.id
                    if items and sent != items[-1].id:
                        yield position(items[-1].id)
            finally:
                for future in (gone, waiting):
                    if future is not None:
                        future.cancel()
                subscription.close()

        headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        return StreamingResponse(events(), media_type="text/event-stream", headers=headers)
