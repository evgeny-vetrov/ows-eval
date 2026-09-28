import json
from pathlib import Path

import ai.gena.services.fabula.server as server_package
from ai.gena.services.fabula.manager.api.openapi import build_openapi
from ai.gena.services.fabula.server.app import create_app
from ai.gena.services.fabula.server.composition import build
from ai.gena.services.fabula.server.openapi import build_server_openapi, render
from ai.gena.services.fabula.server.routes import SEGMENT
from ai.gena.services.fabula.server.testing.harness import make_settings

SNAPSHOT = "ai/gena/services/fabula/server/openapi.json"
UNDOCUMENTED = {"/openapi.json", "/docs", "/redoc"}


def _snapshot() -> str:
    try:
        import yatest.common

        return Path(yatest.common.source_path(SNAPSHOT)).read_text(encoding="utf-8")
    except ImportError:
        return (Path(server_package.__file__).parent / "openapi.json").read_text(encoding="utf-8")


def _refs(value):
    if isinstance(value, dict):
        if "$ref" in value:
            yield value["$ref"]
        for nested in value.values():
            yield from _refs(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _refs(nested)


def test_the_committed_document_is_up_to_date():
    assert _snapshot() == render(), "run `python -m ai.gena.services.fabula.server.openapi > openapi.json`"


def test_the_document_is_self_contained_and_extends_the_managers():
    document = build_server_openapi()
    schemas = document["components"]["schemas"]
    assert {ref.rsplit("/", 1)[1] for ref in _refs(document)} <= set(schemas)
    manager = build_openapi()
    for path, item in manager["paths"].items():
        for method, operation in item.items():
            assert document["paths"][path][method]["operationId"] == operation["operationId"]
            assert {"401", "500"} <= set(document["paths"][path][method]["responses"])
    assert document["security"] == [{"bearer": []}, {"devActor": []}]
    assert document["paths"]["/healthz"]["get"]["security"] == []
    ids = [op["operationId"] for item in document["paths"].values() for op in item.values()]
    assert len(ids) == len(set(ids))


def _routes(app) -> set[tuple[str, str]]:
    found = set()
    for route in app.routes:
        path = route.path.replace(":" + SEGMENT + "}", "}")
        if path in UNDOCUMENTED:
            continue
        found |= {(method, path) for method in route.methods - {"HEAD"}}
    return found


def test_the_document_describes_exactly_the_routes_of_the_application():
    for sandbox in (True, False):
        app = create_app(components=build(make_settings(sandbox={"expose_api": sandbox})))
        document = build_server_openapi(sandbox)
        documented = {(method.upper(), path) for path, item in document["paths"].items() for method in item}
        assert _routes(app) == documented
        assert json.loads(json.dumps(document)) == document
