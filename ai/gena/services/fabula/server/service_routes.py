"""Routes outside the API: liveness, readiness, the OpenAPI document and its viewers."""

from fastapi import FastAPI, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from starlette.responses import HTMLResponse, JSONResponse, Response

from ai.gena.services.fabula.server.errors import problem_response
from ai.gena.services.fabula.server.openapi import build_server_openapi

OPENAPI_PATH = "/openapi.json"


def add_service_routes(app: FastAPI) -> None:
    document: dict = {}

    @app.api_route("/healthz", methods=["GET", "HEAD"], include_in_schema=False)
    async def healthz() -> Response:
        return JSONResponse({"status": "ok"})

    @app.api_route("/readyz", methods=["GET", "HEAD"], include_in_schema=False)
    async def readyz(request: Request) -> Response:
        if not request.app.state.components.ready:
            return problem_response(503, "not_ready", "the server is starting or stopping")
        return JSONResponse({"status": "ready"})

    @app.api_route(OPENAPI_PATH, methods=["GET", "HEAD"], include_in_schema=False)
    async def openapi(request: Request) -> Response:
        if not document:
            components = request.app.state.components
            document.update(build_server_openapi(sandbox=components.info.sandbox))
            root_path = request.scope.get("root_path", "")
            if root_path:
                document["servers"] = [{"url": root_path}]
        return JSONResponse(document)

    if not app.state.components.settings.http.docs:
        return

    @app.get("/docs", include_in_schema=False)
    async def swagger(request: Request) -> HTMLResponse:
        url = request.scope.get("root_path", "") + OPENAPI_PATH
        return get_swagger_ui_html(openapi_url=url, title="Fabula API", swagger_ui_parameters={"persistAuthorization": True})

    @app.get("/redoc", include_in_schema=False)
    async def redoc(request: Request) -> HTMLResponse:
        return get_redoc_html(openapi_url=request.scope.get("root_path", "") + OPENAPI_PATH, title="Fabula API")
