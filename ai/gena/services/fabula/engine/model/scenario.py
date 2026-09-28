from typing import Any

from pydantic import Field

from ai.gena.services.fabula.engine.model.base import Frozen
from ai.gena.services.fabula.engine.model.catalog import CapabilityDescriptor, EventTypeDescriptor
from ai.gena.services.fabula.engine.model.jsonvalue import digest
from ai.gena.services.fabula.engine.version import SCENARIO_SCHEMA_VERSION


class ScenarioSnapshot(Frozen):
    """A validated scenario version with the catalog descriptors it was published against.

    The core never looks at catalogs: running fabulas keep the descriptors they started
    with, so catalog changes do not alter their behaviour or their replay.
    """

    schema_version: int = SCENARIO_SCHEMA_VERSION
    ref: str
    digest: str
    document: dict[str, Any]
    capabilities: dict[str, CapabilityDescriptor] = Field(default_factory=dict)
    events: dict[str, EventTypeDescriptor] = Field(default_factory=dict)

    @classmethod
    def build(
        cls,
        document: dict[str, Any],
        capabilities: dict[str, CapabilityDescriptor],
        events: dict[str, EventTypeDescriptor],
    ) -> "ScenarioSnapshot":
        meta = document.get("document", {})
        ref = f"{meta.get('namespace')}/{meta.get('name')}:{meta.get('version')}"
        body = {
            "document": document,
            "capabilities": {k: v.model_dump(mode="json") for k, v in sorted(capabilities.items())},
            "events": {k: v.model_dump(mode="json") for k, v in sorted(events.items())},
        }
        return cls(
            ref=ref,
            digest=digest(body),
            document=document,
            capabilities=dict(sorted(capabilities.items())),
            events=dict(sorted(events.items())),
        )
