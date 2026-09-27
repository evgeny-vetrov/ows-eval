"""Event matching port (pure).

Contract: `matches(filter, event)` is deterministic, has no side effects and applies the
same semantics as the external subscription system (see `model.filter`), so the core
can re-check deliveries that came through a broader subscription.
"""

from typing import Protocol

from ai.gena.services.fabula.engine.model.event import Event
from ai.gena.services.fabula.engine.model.filter import Filter


class EventMatcher(Protocol):
    def matches(self, filter: Filter, event: Event) -> bool: ...
