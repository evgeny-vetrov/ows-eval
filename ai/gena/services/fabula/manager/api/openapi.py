"""OpenAPI 3.1 document built from the operation table and the pydantic models.

`python -m ai.gena.services.fabula.manager.api.openapi > openapi.json` refreshes the
committed copy; a test keeps the two equal.
"""

import json
import sys
from collections.abc import Iterable
from http import HTTPStatus
from typing import Any

from pydantic.json_schema import models_json_schema

from ai.gena.services.fabula.manager.api.operations import OPERATIONS, Operation
from ai.gena.services.fabula.manager.api.schemas import ProblemDetails

REF = "#/components/schemas/{model}"
API_VERSION = "1.0.0"

TAGS = [
    {"name": "scenarios", "description": "Scenarios, validation, publication and diffs of versions."},
    {"name": "catalogs", "description": "Capabilities and event types a scenario may use."},
    {"name": "tags", "description": "Tags route new starts to versions: move, roll back, split for A/B experiments."},
    {"name": "fabulas", "description": "Start, find and inspect fabulas; intervene."},
    {"name": "migrations", "description": "Move running fabulas to another version: analysis, clusters, decisions, application."},
    {"name": "agentic-resolutions", "description": "Tasks through which trusted agents resolve migration conflicts."},
    {"name": "audit", "description": "Who did what, when and why."},
]

ERROR_DESCRIPTIONS = {
    400: "The request does not match the schema",
    403: "The actor lacks the permission",
    404: "Not found",
    409: "Conflicts with the current state",
    412: "expected_revision is stale",
    422: "Well formed but cannot be carried out",
}

DESCRIPTION = """\
Management of fabulas: scenario versions, tags, running processes, migrations between
versions and their conflict resolution. Every mutation takes a caller-chosen
`request_id`; repeating it is harmless. The actor comes from the authentication of the
transport; each operation names the permission it needs in `x-permission`. Errors are
RFC 7807 problems with a stable `code`.
"""


def _query_parameters(model: Any, defs: dict[str, Any]) -> list[dict[str, Any]]:
    schema = model.model_json_schema(ref_template=REF)
    defs.update(schema.pop("$defs", {}))
    required = set(schema.get("required", ()))
    parameters = []
    for name, field_schema in schema["properties"].items():
        parameter = {"name": name, "in": "query", "required": name in required, "schema": field_schema}
        if field_schema.get("type") == "array":
            parameter.update(style="form", explode=True)
        parameters.append(parameter)
    return parameters


def _error_description(code: int) -> str:
    return ERROR_DESCRIPTIONS.get(code) or HTTPStatus(code).phrase


def build_openapi(operations: Iterable[Operation] = OPERATIONS, tags: list[dict[str, str]] = TAGS) -> dict[str, Any]:
    """The document of the manager's operations; a host passes its own additions."""
    operations = tuple(operations)
    pairs = [(ProblemDetails, "serialization")]
    for op in operations:
        if op.body is not None:
            pairs.append((op.body, "validation"))
        pairs.append((op.response, "serialization"))
    pairs = list(dict.fromkeys(pairs))
    refs, top = models_json_schema(pairs, ref_template=REF)
    schemas = dict(top.get("$defs", {}))
    paths: dict[str, dict[str, Any]] = {}
    for op in operations:
        parameters = [{"name": name, "in": "path", "required": True, "schema": {"type": "string"}} for name in op.path_params]
        if op.query is not None:
            parameters += _query_parameters(op.query, schemas)
        responses = {str(op.status): {"description": "OK", "content": {"application/json": {"schema": refs[(op.response, "serialization")]}}}}
        if op.handler == "start_fabula":
            responses["200"] = {"description": "The fabula this request started earlier", "content": {"application/json": {"schema": refs[(op.response, "serialization")]}}}
        for code in sorted({400, *op.errors} | ({403} if op.permission is not None else set())):
            responses[str(code)] = {
                "description": _error_description(code),
                "content": {"application/problem+json": {"schema": refs[(ProblemDetails, "serialization")]}},
            }
        operation: dict[str, Any] = {"operationId": op.operation_id, "summary": op.summary, "tags": [op.tag]}
        if op.permission is not None:
            operation["x-permission"] = op.permission
        operation["responses"] = dict(sorted(responses.items()))
        if parameters:
            operation["parameters"] = parameters
        if op.body is not None:
            operation["requestBody"] = {"required": True, "content": {"application/json": {"schema": refs[(op.body, "validation")]}}}
        paths.setdefault(op.path, {})[op.method.lower()] = operation
    return {
        "openapi": "3.1.0",
        "info": {"title": "Fabula manager API", "version": API_VERSION, "description": DESCRIPTION},
        "tags": tags,
        "paths": paths,
        "components": {"schemas": dict(sorted(schemas.items()))},
    }


def render() -> str:
    return json.dumps(build_openapi(), indent=2, sort_keys=False, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    sys.stdout.write(render())
