"""The wire format without a transport.

`Dispatcher.dispatch(operation_id, actor=..., path=..., query=..., body=...)` takes
JSON-shaped input (query values as strings or lists of strings), validates it,
checks the actor's right, calls the facade and returns the status and JSON body. Any
error becomes an RFC 7807 problem. `Dispatcher.route` maps an HTTP method and path to
an operation of the manager, so an HTTP adapter is a few lines in any framework.

A host may dispatch its own operations (same `Operation` rows, its own facade) through
another `Dispatcher` built with `operations=`; `dispatcher.match` routes within that
table.
"""

import json
import re
import typing
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import TypeAdapter, ValidationError

from ai.gena.services.fabula.engine.ports.storage import FabulaNotFound
from ai.gena.services.fabula.manager.api.operations import OPERATIONS, Operation
from ai.gena.services.fabula.manager.api.schemas import ApiResult
from ai.gena.services.fabula.manager.model.common import Actor
from ai.gena.services.fabula.manager.model.errors import Forbidden, InvalidInput, ManagerError, NotFound
from ai.gena.services.fabula.manager.ports.system import Authorizer

PROBLEM_TYPE = "urn:fabula:problem:"


@dataclass(frozen=True)
class ApiResponse:
    status: int
    body: Any
    content_type: str = "application/json"


def problem(status: int, code: str, detail: str, **extensions: Any) -> ApiResponse:
    body = {"type": PROBLEM_TYPE + code, "title": code.replace("_", " "), "status": status, "detail": detail, "code": code, **extensions}
    return ApiResponse(status=status, body=body, content_type="application/problem+json")


def _is_sequence(annotation: Any) -> bool:
    return typing.get_origin(annotation) in (tuple, list)


def parse_query(model: Any, query: dict[str, Any]) -> Any:
    data: dict[str, Any] = {}
    fields = model.model_fields
    for key, raw in query.items():
        values = list(raw) if isinstance(raw, (list, tuple)) else [raw]
        field = fields.get(key)
        data[key] = values if field is not None and _is_sequence(field.annotation) else values[-1]
    return model.model_validate(data)


def _route_pattern(template: str) -> re.Pattern:
    """`{param}` matches one path segment without ':' (it starts a custom verb, as in
    `/v1/migrations/{id}:apply`). The one exception is a trailing `{request_id}`:
    request ids may hold ':' (`mig:<campaign>:<fabula>:1:swap`)."""
    parts = re.split(r"({\w+})", template)
    regex = ""
    for index, part in enumerate(parts):
        if not part.startswith("{"):
            regex += re.escape(part)
        elif part == "{request_id}" and index == len(parts) - 2 and parts[-1] == "":
            regex += "(?P<request_id>[^/]+)"
        else:
            regex += f"(?P<{part[1:-1]}>[^/:]+)"
    return re.compile(f"^{regex}$")


def has_custom_verb(op: Operation) -> bool:
    return ":" in re.sub(r"{\w+}", "", op.path)


def in_routing_order(operations: Iterable[Operation]) -> list[Operation]:
    """Templates with a custom verb first: `/tags/{tag}:resolve` before `/tags/{tag}`."""
    return sorted(operations, key=lambda op: not has_custom_verb(op))


Routes = list[tuple[str, re.Pattern, Operation]]


def _routes(operations: Iterable[Operation]) -> Routes:
    return [(op.method, _route_pattern(op.path), op) for op in in_routing_order(operations)]


def _match(routes: Routes, method: str, path: str) -> tuple[Operation, dict[str, str]] | None:
    for route_method, pattern, op in routes:
        match = pattern.match(path)
        if match and route_method == method.upper():
            return op, match.groupdict()
    return None


_ROUTES = _routes(OPERATIONS)


class Dispatcher:
    def __init__(self, api: Any, authorizer: Authorizer, operations: Iterable[Operation] = OPERATIONS):
        """`api` has a method per `Operation.handler`; `FabulaApi` for the manager's table."""
        self.api = api
        self.authorizer = authorizer
        self.operations = tuple(operations)
        self._by_id = {op.operation_id: op for op in self.operations}
        if len(self._by_id) != len(self.operations):
            raise ValueError("operation ids must be unique")
        missing = [op.handler for op in self.operations if not callable(getattr(api, op.handler, None))]
        if missing:
            raise ValueError(f"the api lacks handlers: {', '.join(missing)}")
        self._routes = _routes(self.operations)

    @staticmethod
    def route(method: str, path: str) -> tuple[Operation, dict[str, str]] | None:
        """An operation of the manager's table."""
        return _match(_ROUTES, method, path)

    def match(self, method: str, path: str) -> tuple[Operation, dict[str, str]] | None:
        """An operation of this dispatcher's table."""
        return _match(self._routes, method, path)

    async def dispatch(
        self,
        operation_id: str,
        *,
        actor: Actor,
        path: dict[str, str] | None = None,
        query: dict[str, Any] | None = None,
        body: Any = None,
    ) -> ApiResponse:
        op = self._by_id.get(operation_id)
        if op is None:
            return problem(404, "unknown_operation", f"there is no operation {operation_id}")
        try:
            return await self._call(op, actor, dict(path or {}), query or {}, body)
        except ValidationError as exc:
            return problem(400, "invalid_input", "the request does not match the schema", errors=json.loads(exc.json(include_url=False)))
        except ManagerError as exc:
            return problem(exc.status, exc.code, exc.message, **exc.details)
        except FabulaNotFound as exc:
            return problem(404, "fabula_not_found", f"the runtime does not know fabula {exc}")

    async def _call(self, op: Operation, actor: Actor, path: dict[str, str], query: dict[str, Any], body: Any) -> ApiResponse:
        missing = [p for p in op.path_params if not path.get(p)]
        if missing:
            raise InvalidInput(f"missing path parameters: {', '.join(missing)}", code="missing_path_parameter")
        if op.query is None and query:
            raise InvalidInput(f"{op.operation_id} takes no query parameters", code="unexpected_query")
        if op.body is None and body not in (None, {}):
            raise InvalidInput(f"{op.operation_id} takes no body", code="unexpected_body")
        parsed_query = parse_query(op.query, query) if op.query is not None else None
        parsed_body = op.body.model_validate(body if body is not None else {}) if op.body is not None else None
        resource = op.resource(path, parsed_body)
        if op.permission is not None and not await self.authorizer.allowed(actor, op.permission, resource):
            raise Forbidden(f"{actor} lacks {op.permission} on {resource}", code="forbidden")
        result = await getattr(self.api, op.handler)(actor, path, parsed_query, parsed_body)
        status = op.status
        if isinstance(result, ApiResult):
            status, result = result.status, result.value
        if result is None:
            raise NotFound("nothing to return", code="not_found")
        return ApiResponse(status=status, body=TypeAdapter(op.response).dump_python(result, mode="json"))
