from ai.gena.services.fabula.manager.model.audit import ManagerEvent
from ai.gena.services.fabula.manager.model.common import Actor
from ai.gena.services.fabula.manager.ports.system import PERMISSIONS


class AllowAll:
    async def allowed(self, actor: Actor, permission: str, resource: str) -> bool:
        return True


class RoleAuthorizer:
    """Permissions by actor id, for tests of the API boundary."""

    def __init__(self, grants: dict[str, set[str]]):
        unknown = {p for granted in grants.values() for p in granted} - set(PERMISSIONS)
        if unknown:
            raise ValueError(f"unknown permissions: {sorted(unknown)}")
        self.grants = grants

    async def allowed(self, actor: Actor, permission: str, resource: str) -> bool:
        return permission in self.grants.get(actor.id, set())


class SequentialIds:
    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def new(self, prefix: str) -> str:
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        return f"{prefix}-{self._counters[prefix]}"


class RecordingNotifier:
    def __init__(self) -> None:
        self.events: list[ManagerEvent] = []

    async def notify(self, event: ManagerEvent) -> None:
        self.events.append(event)

    def kinds(self) -> list[str]:
        return [event.kind for event in self.events]
