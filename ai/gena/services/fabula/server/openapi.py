"""The OpenAPI document of the server: the manager's operations, the session and the
sandbox, plus what only HTTP has (authentication, transport errors, the event stream,
health checks).

`python -m ai.gena.services.fabula.server.openapi > openapi.json` refreshes the
committed copy (with the sandbox); a test keeps the two equal.
"""

import json
import sys
from typing import Any

from pydantic.json_schema import models_json_schema

from ai.gena.services.fabula.manager.api.openapi import API_VERSION, REF, build_openapi
from ai.gena.services.fabula.manager.model.audit import ManagerEvent
from ai.gena.services.fabula.server.operations import SERVER_TAGS, server_operations
from ai.gena.services.fabula.server.stream import READ_PERMISSIONS, STREAM_PATH

DESCRIPTION = """\
Management of fabulas over HTTP: scenario versions, tags, running processes, migrations
between versions and their conflict resolution; live notifications for the web UI.

* **Authentication.** `Authorization: Bearer <token>`; a development server may instead
  trust `X-Fabula-Actor: user:<id>`. `GET /v1/me` tells who the caller is and what it
  may do. The event stream also takes `?access_token=` (or `?actor=`), since a
  browser's `EventSource` cannot set headers.
* **Idempotency.** Every mutation takes a caller-chosen `request_id`; repeating it is
  harmless. Optimistic locking goes through `expected_revision`.
* **Errors.** RFC 7807 problems (`application/problem+json`) with a stable `code`.
* **Tracing.** `X-Trace-Id` is taken from the request or made up, and returned; a 500
  problem carries it as `trace_id`.
* **Live updates.** `GET /v1/notifications:stream` (server-sent events).
"""

SERVICE_TAG = {"name": "service", "description": "Health of the server."}
NOTIFICATIONS_TAG = {"name": "notifications", "description": "Live updates for the web UI."}

SECURITY_SCHEMES = {
    "bearer": {"type": "http", "scheme": "bearer", "description": "A token issued for the actor."},
    "devActor": {
        "type": "apiKey",
        "in": "header",
        "name": "X-Fabula-Actor",
        "description": "Development servers only: the caller names itself, `user:alice`, `agent:resolver-1`.",
    },
}

PROBLEM = {"application/problem+json": {"schema": {"$ref": REF.format(model="ProblemDetails")}}}
TRANSPORT_ERRORS = {"401": "Not authenticated", "500": "The server failed; see trace_id"}
BODY_ERRORS = {"413": "The body is too large", "415": "The body is not JSON"}


def _stream_path(schemas: dict[str, Any]) -> dict[str, Any]:
    _, top = models_json_schema([(ManagerEvent, "serialization")], ref_template=REF)
    schemas.update(top.get("$defs", {}))
    rights = ", ".join(f"`{prefix}` needs `{permission}`" for prefix, permission in READ_PERMISSIONS)
    description = (
        "Server-sent events. Each notification is `id: <boot>.<seq>`, `event: <kind>` and "
        "`data:` a JSON `ManagerEvent`. Kinds: the manager's (`version.published`, `tag.moved`, "
        "`campaign.awaiting_decision`, `resolution.task_open`, `proposal.awaiting_approval`, ...) and "
        "`fabula.changed` after every step of a fabula. `event: reset` means notifications were "
        "missed (buffer overrun, server restart): refetch what is shown. A comment line is sent "
        f"while idle. Only notifications the actor may read arrive: {rights}, anything else `audit.read`."
    )
    parameters = [
        {
            "name": "kind",
            "in": "query",
            "required": False,
            "description": "Kinds to receive; globs allowed (`fabula.*`).",
            "schema": {"type": "array", "items": {"type": "string"}},
            "style": "form",
            "explode": True,
        },
        {"name": "resource", "in": "query", "required": False, "description": "Prefix of the resource (`fabula:f-1`, `migration:`).", "schema": {"type": "string"}},
        {"name": "Last-Event-ID", "in": "header", "required": False, "description": "Resume after this notification.", "schema": {"type": "string"}},
        {"name": "last_event_id", "in": "query", "required": False, "description": "Same as the header, for clients that cannot set it.", "schema": {"type": "string"}},
        {"name": "access_token", "in": "query", "required": False, "description": "Bearer token, for `EventSource`.", "schema": {"type": "string"}},
        {"name": "actor", "in": "query", "required": False, "description": "Development servers: the actor, for `EventSource`.", "schema": {"type": "string"}},
    ]
    return {
        "get": {
            "operationId": "streamNotifications",
            "summary": "Live notifications (server-sent events)",
            "description": description,
            "tags": ["notifications"],
            "parameters": parameters,
            "responses": {
                "200": {
                    "description": "An endless stream of notifications",
                    "content": {"text/event-stream": {"schema": {"type": "string"}, "x-event-schema": {"$ref": REF.format(model="ManagerEvent")}}},
                },
                "401": {"description": TRANSPORT_ERRORS["401"], "content": PROBLEM},
            },
        }
    }


def _health_paths() -> dict[str, Any]:
    ok = {"application/json": {"schema": {"type": "object", "properties": {"status": {"type": "string"}}, "required": ["status"]}}}
    return {
        "/healthz": {"get": {"operationId": "healthz", "summary": "The process is alive", "tags": ["service"], "security": [], "responses": {"200": {"description": "Alive", "content": ok}}}},
        "/readyz": {
            "get": {
                "operationId": "readyz",
                "summary": "The server has started and serves requests",
                "tags": ["service"],
                "security": [],
                "responses": {"200": {"description": "Ready", "content": ok}, "503": {"description": "Starting or stopping", "content": PROBLEM}},
            }
        },
    }


def build_server_openapi(sandbox: bool = True) -> dict[str, Any]:
    tags = [tag for tag in SERVER_TAGS if sandbox or tag["name"] != "sandbox"] + [NOTIFICATIONS_TAG, SERVICE_TAG]
    document = build_openapi(server_operations(sandbox), tags)
    document["info"] = {"title": "Fabula server API", "version": API_VERSION, "description": DESCRIPTION}
    for item in document["paths"].values():
        for method, operation in item.items():
            responses = operation["responses"]
            # Every request but GET may carry a body, and every such body is checked.
            errors = {**TRANSPORT_ERRORS, **(BODY_ERRORS if method != "get" else {})}
            for code, description in errors.items():
                responses.setdefault(code, {"description": description, "content": PROBLEM})
            operation["responses"] = dict(sorted(responses.items()))
    schemas = document["components"]["schemas"]
    document["paths"][STREAM_PATH] = _stream_path(schemas)
    document["paths"].update(_health_paths())
    document["components"]["schemas"] = dict(sorted(schemas.items()))
    document["components"]["securitySchemes"] = SECURITY_SCHEMES
    document["security"] = [{"bearer": []}, {"devActor": []}]
    return document


def render(sandbox: bool = True) -> str:
    return json.dumps(build_server_openapi(sandbox), indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    sys.stdout.write(render())
