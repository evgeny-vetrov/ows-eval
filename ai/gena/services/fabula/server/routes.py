"""FastAPI routes generated from the operation table.

One route per path serves all its methods, so a wrong method gets 405 with the full
`Allow`. The endpoint only moves the request into `Dispatcher.dispatch` and its answer
back: validation, rights and errors stay where they are for every transport.
"""

import json
import logging
import re
from typing import Any

from fastapi import Depends, FastAPI, Request
from starlette.convertors import Convertor, register_url_convertor
from starlette.responses import Response

from ai.gena.services.fabula.manager.api.dispatch import in_routing_order
from ai.gena.services.fabula.manager.api.operations import Operation
from ai.gena.services.fabula.manager.model.common import Actor
from ai.gena.services.fabula.server.dependencies import current_actor
from ai.gena.services.fabula.server.errors import OPERATION_HEADER, HttpProblem, json_response, trace_id

log = logging.getLogger(__name__)

SEGMENT = "fabula_segment"


class SegmentConvertor(Convertor):
    """A path segment without ':', which starts a custom verb (`/v1/migrations/{id}:apply`)."""

    regex = "[^/:]+"

    def convert(self, value: str) -> str:
        return value

    def to_string(self, value: str) -> str:
        return value


register_url_convertor(SEGMENT, SegmentConvertor())


def starlette_path(template: str) -> str:
    """The route of an operation path, matching as `Dispatcher.route` does: a trailing
    `{request_id}` may hold ':' (`mig:<campaign>:<fabula>:1:swap`), other parameters
    may not."""
    parts = re.split(r"({\w+})", template)
    path = ""
    for index, part in enumerate(parts):
        if not part.startswith("{"):
            path += part
        elif part == "{request_id}" and index == len(parts) - 2 and parts[-1] == "":
            path += part
        else:
            path += "{" + part[1:-1] + ":" + SEGMENT + "}"
    return path


def _not_json(constant: str) -> Any:
    raise ValueError(f"{constant} is not a JSON value")


async def read_json(request: Request, limit: int) -> Any:
    """The JSON body, or None when there is none.

    A body must say it is JSON: a cross-site form or `no-cors` fetch cannot, so this is
    what keeps other sites from calling a development server as its default actor."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise HttpProblem(413, "body_too_large", f"the body exceeds {limit} bytes")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HttpProblem(413, "body_too_large", f"the body exceeds {limit} bytes")
        chunks.append(chunk)
    raw = b"".join(chunks)
    if not raw.strip():
        return None
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json" and not media_type.endswith("+json"):
        raise HttpProblem(415, "unsupported_media_type", f"the body must be application/json, not {media_type or 'untyped'}")
    try:
        return json.loads(raw, parse_constant=_not_json)
    except (UnicodeDecodeError, ValueError) as exc:
        raise HttpProblem(400, "invalid_json", f"the body is not JSON: {exc}") from None


def _query(request: Request) -> dict[str, list[str]]:
    return {key: request.query_params.getlist(key) for key in request.query_params.keys()}


def _operation(operations: dict[str, Operation], request: Request) -> Operation:
    return operations["GET" if request.method == "HEAD" else request.method]


def _marker(operations: dict[str, Operation]):
    async def mark(request: Request) -> None:
        # Runs before authentication, so even a 401 names the operation.
        request.state.operation = _operation(operations, request).operation_id

    return mark


def _endpoint(operations: dict[str, Operation]):
    async def endpoint(request: Request, actor: Actor = Depends(current_actor)) -> Response:
        op = _operation(operations, request)
        components = request.app.state.components
        body = None
        if request.method not in ("GET", "HEAD"):
            body = await read_json(request, components.settings.http.max_body_bytes)
        try:
            response = await components.dispatcher.dispatch(op.operation_id, actor=actor, path=dict(request.path_params), query=_query(request), body=body)
            return json_response(response, headers={OPERATION_HEADER: op.operation_id})
        except Exception:
            log.exception("%s failed; trace %s", op.operation_id, trace_id(request))
            raise HttpProblem(500, "internal", "the server failed; the trace id identifies the failure in its logs", trace_id=trace_id(request)) from None

    return endpoint


def add_operation_routes(app: FastAPI, operations: tuple[Operation, ...]) -> None:
    by_path: dict[str, dict[str, Operation]] = {}
    for op in in_routing_order(operations):
        by_path.setdefault(op.path, {})[op.method] = op
    for path, by_method in by_path.items():
        # FastAPI, unlike Starlette, does not answer HEAD on GET routes by itself.
        methods = [*by_method, *(["HEAD"] if "GET" in by_method else [])]
        app.add_api_route(
            starlette_path(path),
            _endpoint(by_method),
            methods=methods,
            dependencies=[Depends(_marker(by_method))],
            include_in_schema=False,
            response_model=None,
            name="+".join(op.operation_id for op in by_method.values()),
        )
