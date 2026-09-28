from __future__ import annotations

from ai.gena.services.fabula.engine.model.catalog import CapabilityDescriptor, EventTypeDescriptor


class InMemoryCapabilityDirectory:
    def __init__(self, descriptors: list[CapabilityDescriptor] | None = None):
        self._descriptors = {d.ref: d for d in descriptors or []}

    def add(self, descriptor: CapabilityDescriptor) -> None:
        self._descriptors[descriptor.ref] = descriptor

    def resolve(self, ref: str) -> CapabilityDescriptor | None:
        return self._descriptors.get(ref)

    async def list(self, query: str | None = None) -> list[CapabilityDescriptor]:
        return [d for ref, d in sorted(self._descriptors.items()) if query is None or query.lower() in ref.lower() or query.lower() in (d.title or "").lower()]


class InMemoryEventDirectory:
    def __init__(self, descriptors: list[EventTypeDescriptor] | None = None):
        self._descriptors = {d.type: d for d in descriptors or []}

    def add(self, descriptor: EventTypeDescriptor) -> None:
        self._descriptors[descriptor.type] = descriptor

    def resolve(self, event_type: str) -> EventTypeDescriptor | None:
        return self._descriptors.get(event_type)

    async def list(self, query: str | None = None) -> list[EventTypeDescriptor]:
        return [d for key, d in sorted(self._descriptors.items()) if query is None or query.lower() in key.lower()]
