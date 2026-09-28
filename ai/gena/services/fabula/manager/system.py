"""Default implementations of the system ports that need no infrastructure."""

import uuid

from ai.gena.services.fabula.manager.model.audit import ManagerEvent


class UuidIds:
    def new(self, prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex}"


class NoopNotifier:
    async def notify(self, event: ManagerEvent) -> None:
        return None
