"""The FastAPI application.

`create_app(settings)` builds the in-memory profile and the application around it;
`uvicorn --factory ai.gena.services.fabula.server.app:app_from_env` reads the settings
file named by `FABULA_SERVER_CONFIG`.
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from ai.gena.services.fabula.manager.api.openapi import API_VERSION
from ai.gena.services.fabula.server.composition import Components, build
from ai.gena.services.fabula.server.errors import OPERATION_HEADER, install_error_handlers
from ai.gena.services.fabula.server.middleware import TRACE_HEADER, GZipExceptStreams, TraceMiddleware, TrustedHosts
from ai.gena.services.fabula.server.routes import add_operation_routes
from ai.gena.services.fabula.server.service_routes import add_service_routes
from ai.gena.services.fabula.server.settings import ServerSettings, load_settings
from ai.gena.services.fabula.server.stream import add_stream_route


def create_app(settings: ServerSettings | None = None, components: Components | None = None) -> FastAPI:
    components = components or build(settings or ServerSettings())
    settings = components.settings

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await components.start()
        try:
            yield
        finally:
            await components.stop()

    app = FastAPI(
        title="Fabula server API",
        version=API_VERSION,
        # The document is the manager's own (see openapi.py), not FastAPI's.
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        root_path=settings.http.root_path,
        lifespan=lifespan,
    )
    app.state.components = components
    install_error_handlers(app)
    add_service_routes(app)
    add_stream_route(app)
    add_operation_routes(app, components.dispatcher.operations)

    # Added innermost first: compression, CORS, trusted hosts, tracing around everything.
    app.add_middleware(GZipExceptStreams)
    if settings.http.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.http.cors_origins),
            allow_methods=sorted({op.method for op in components.dispatcher.operations}),
            allow_headers=["Authorization", "Content-Type", "X-Fabula-Actor", TRACE_HEADER, "Last-Event-ID"],
            expose_headers=[TRACE_HEADER, OPERATION_HEADER],
            max_age=600,
        )
    app.add_middleware(TrustedHosts, patterns=settings.http.trusted_hosts(components.authenticator.mode))
    app.add_middleware(TraceMiddleware)
    return app


def app_from_env() -> FastAPI:
    return create_app(load_settings(os.environ.get("FABULA_SERVER_CONFIG")))
