"""FastAPI dependencies: the authenticated actor of a request."""

from fastapi import Request

from ai.gena.services.fabula.manager.model.common import Actor
from ai.gena.services.fabula.server.auth import Credentials


async def _authenticate(request: Request, query_allowed: bool) -> Actor:
    components = request.app.state.components
    credentials = Credentials(headers=dict(request.headers.items()), query=dict(request.query_params.items()), query_allowed=query_allowed)
    actor = await components.authenticator.authenticate(credentials)
    request.state.actor = str(actor)
    return actor


async def current_actor(request: Request) -> Actor:
    return await _authenticate(request, query_allowed=False)


async def stream_actor(request: Request) -> Actor:
    """Streams also accept `?access_token=` / `?actor=`: `EventSource` sets no headers."""
    return await _authenticate(request, query_allowed=True)
