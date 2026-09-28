"""Rights, identifiers and notifications."""

from typing import Protocol

from ai.gena.services.fabula.manager.model.audit import ManagerEvent
from ai.gena.services.fabula.manager.model.common import Actor

# Permissions checked at the API boundary; `resource` names the object
# (`scenario:<ns/name>`, `fabula:<id>`, `migration:<id>`, `resolution-task:<id>`, `*`).
PERMISSIONS = (
    "scenario.read",
    "scenario.write",
    "scenario.publish",
    "tag.write",
    "fabula.read",
    "fabula.start",
    "fabula.control",
    "migration.read",
    "migration.write",
    "resolution.work",
    "resolution.approve",
    "audit.read",
)


class Authorizer(Protocol):
    async def allowed(self, actor: Actor, permission: str, resource: str) -> bool: ...


class IdSource(Protocol):
    def new(self, prefix: str) -> str:
        """A fresh unique id starting with `prefix-`."""
        ...


class Notifier(Protocol):
    """Delivers manager events at least once; failures must not break the caller."""

    async def notify(self, event: ManagerEvent) -> None: ...
