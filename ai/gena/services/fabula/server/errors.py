"""Every error leaves the server as an RFC 7807 problem (`application/problem+json`)
with a stable `code`, as the manager's own errors do."""

import json
import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from ai.gena.services.fabula.manager.api.dispatch import ApiResponse, problem
from ai.gena.services.fabula.server.auth import Unauthenticated

log = logging.getLogger(__name__)

OPERATION_HEADER = "X-Fabula-Operation"


class HttpProblem(Exception):
    """A problem found by the HTTP layer itself (body too large, not JSON, ...)."""

    def __init__(self, status: int, code: str, detail: str, headers: dict[str, str] | None = None, **extensions: Any):
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail
        self.headers = headers
        self.extensions = extensions


def json_response(response: ApiResponse, headers: dict[str, str] | None = None) -> Response:
    # NaN and Infinity are not JSON; a browser's JSON.parse rejects them.
    content = json.dumps(response.body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return Response(content=content, status_code=response.status, media_type=response.content_type, headers=headers)


def problem_response(status: int, code: str, detail: str, headers: dict[str, str] | None = None, **extensions: Any) -> Response:
    return json_response(problem(status, code, detail, **extensions), headers)


def trace_id(request: Request) -> str:
    return getattr(request.state, "trace_id", "")


def operation_headers(request: Request, headers: dict[str, str] | None = None) -> dict[str, str]:
    """`headers` plus `X-Fabula-Operation` once the route has named the operation."""
    operation = getattr(request.state, "operation", None)
    return {**(headers or {}), **({OPERATION_HEADER: operation} if operation else {})}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HttpProblem)
    async def _http_problem(request: Request, exc: HttpProblem) -> Response:
        return problem_response(exc.status, exc.code, exc.detail, operation_headers(request, exc.headers), **exc.extensions)

    @app.exception_handler(Unauthenticated)
    async def _unauthenticated(request: Request, exc: Unauthenticated) -> Response:
        mode = request.app.state.components.authenticator.mode
        headers = {"WWW-Authenticate": 'Bearer realm="fabula"'} if mode == "tokens" else None
        return problem_response(401, "unauthenticated", str(exc), operation_headers(request, headers))

    @app.exception_handler(StarletteHTTPException)
    async def _starlette(request: Request, exc: StarletteHTTPException) -> Response:
        if exc.status_code == 404:
            return problem_response(404, "route_not_found", f"no operation at {request.url.path}")
        if exc.status_code == 405:
            return problem_response(405, "method_not_allowed", f"{request.url.path} does not take {request.method}", exc.headers)
        code = HTTPStatus(exc.status_code).phrase.lower().replace(" ", "_").replace("-", "_")
        return problem_response(exc.status_code, code, str(exc.detail), exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> Response:
        return problem_response(400, "invalid_input", "the request does not match the schema", errors=json.loads(json.dumps(exc.errors(), default=str)))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> Response:
        # Last resort outside the other middleware; endpoints turn their own failures
        # into problems so that those still carry the trace id and CORS headers.
        return problem_response(500, "internal", "the server failed; the trace id identifies the failure in its logs", trace_id=trace_id(request))
