"""Browsable catalogs of capabilities and event types.

`resolve` has the signature of the engine's `CapabilityCatalog` / `EventCatalog`, so a
directory can be passed straight to the DSL validator. `list` serves editors.
"""

from __future__ import annotations

from typing import Protocol

from ai.gena.services.fabula.engine.model.catalog import CapabilityDescriptor, EventTypeDescriptor


class CapabilityDirectory(Protocol):
    def resolve(self, ref: str) -> CapabilityDescriptor | None: ...

    async def list(self, query: str | None = None) -> list[CapabilityDescriptor]: ...


class EventDirectory(Protocol):
    def resolve(self, event_type: str) -> EventTypeDescriptor | None: ...

    async def list(self, query: str | None = None) -> list[EventTypeDescriptor]: ...
