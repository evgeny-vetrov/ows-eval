from ai.gena.services.fabula.engine.model.catalog import CapabilityDescriptor, EventTypeDescriptor


class InMemoryCapabilityCatalog:
    def __init__(self, descriptors: list[CapabilityDescriptor] | None = None):
        self._descriptors = {d.ref: d for d in descriptors or []}

    def add(self, descriptor: CapabilityDescriptor) -> None:
        self._descriptors[descriptor.ref] = descriptor

    def resolve(self, ref: str) -> CapabilityDescriptor | None:
        return self._descriptors.get(ref)


class InMemoryEventCatalog:
    def __init__(self, descriptors: list[EventTypeDescriptor] | None = None):
        self._descriptors = {d.type: d for d in descriptors or []}

    def add(self, descriptor: EventTypeDescriptor) -> None:
        self._descriptors[descriptor.type] = descriptor

    def resolve(self, event_type: str) -> EventTypeDescriptor | None:
        return self._descriptors.get(event_type)
