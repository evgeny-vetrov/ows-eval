"""Catalog ports, used by the DSL validator and by the host (never by the core).

Contract: `resolve` is a read without side effects. For a published scenario the
validator pins every resolved descriptor into the `ScenarioSnapshot`, so later catalog
changes do not affect running fabulas.
"""

from collections.abc import Mapping
from typing import Protocol

from ai.gena.services.fabula.engine.model.catalog import CapabilityDescriptor, EventTypeDescriptor


class CapabilityCatalog(Protocol):
    def resolve(self, ref: str) -> CapabilityDescriptor | None:
        """`ref` has the form `name:version@provider`."""
        ...


class EventCatalog(Protocol):
    def resolve(self, event_type: str) -> EventTypeDescriptor | None: ...


class PinnedCapabilities:
    """Capability catalog over descriptors pinned in a snapshot."""

    def __init__(self, descriptors: Mapping[str, CapabilityDescriptor]):
        self._descriptors = descriptors

    def resolve(self, ref: str) -> CapabilityDescriptor | None:
        return self._descriptors.get(ref)


class PinnedEvents:
    """Event catalog over descriptors pinned in a snapshot."""

    def __init__(self, descriptors: Mapping[str, EventTypeDescriptor]):
        self._descriptors = descriptors

    def resolve(self, event_type: str) -> EventTypeDescriptor | None:
        return self._descriptors.get(event_type)
