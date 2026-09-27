"""Loading persisted states written by older engine versions.

A layout change bumps `STATE_SCHEMA_VERSION` and adds a function that turns the JSON of
version N into version N + 1. Loading applies the chain and only then validates.
"""

import json
from collections.abc import Callable
from typing import Any

from ai.gena.services.fabula.engine.core.interpreter import InvalidState
from ai.gena.services.fabula.engine.core.state import FabulaState
from ai.gena.services.fabula.engine.version import STATE_SCHEMA_VERSION

MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}


def dump_state(state: FabulaState) -> dict[str, Any]:
    return state.model_dump(mode="json")


def load_state(document: dict[str, Any] | str) -> FabulaState:
    data = json.loads(document) if isinstance(document, str) else dict(document)
    version = data.get("schema_version")
    if not isinstance(version, int) or version < 1:
        raise InvalidState(f"state has no valid schema_version: {version!r}")
    if version > STATE_SCHEMA_VERSION:
        raise InvalidState(f"state schema {version} is newer than this engine ({STATE_SCHEMA_VERSION})")
    while version < STATE_SCHEMA_VERSION:
        data = MIGRATIONS[version](data)
        version += 1
        data["schema_version"] = version
    return FabulaState.model_validate(data)
