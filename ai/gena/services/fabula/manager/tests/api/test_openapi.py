import json
from pathlib import Path

import ai.gena.services.fabula.manager as manager
from ai.gena.services.fabula.manager.api.openapi import build_openapi, render
from ai.gena.services.fabula.manager.api.operations import OPERATIONS

SNAPSHOT = "ai/gena/services/fabula/manager/openapi.json"


def _snapshot() -> str:
    try:
        import yatest.common

        return Path(yatest.common.source_path(SNAPSHOT)).read_text(encoding="utf-8")
    except ImportError:
        return (Path(manager.__file__).parent / "openapi.json").read_text(encoding="utf-8")


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
    assert _snapshot() == render(), "run `python -m ai.gena.services.fabula.manager.api.openapi > openapi.json`"


def test_the_document_is_complete_and_self_contained():
    document = build_openapi()
    assert document["openapi"] == "3.1.0"
    operations = [op for item in document["paths"].values() for op in item.values()]
    assert len(operations) == len(OPERATIONS)
    assert len({op["operationId"] for op in operations}) == len(OPERATIONS)
    schemas = document["components"]["schemas"]
    for ref in set(_refs(document)):
        assert ref.startswith("#/components/schemas/") and ref.rsplit("/", 1)[1] in schemas, ref
    for op in operations:
        assert "400" in op["responses"] and "403" in op["responses"] and op["x-permission"]
    json.dumps(document)
