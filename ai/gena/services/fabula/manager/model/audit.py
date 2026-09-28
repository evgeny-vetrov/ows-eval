from typing import Any

from pydantic import Field

from ai.gena.services.fabula.engine.model.base import Frozen, UtcDatetime


class AuditRecord(Frozen):
    record_id: str
    at: UtcDatetime
    actor: str
    action: str
    resource: str  # scenario:<ns/name>, fabula:<id>, migration:<id>, resolution-task:<id>
    request_id: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class AuditPage(Frozen):
    items: tuple[AuditRecord, ...]


class ManagerEvent(Frozen):
    """Notification for the web and other systems (campaign awaiting a decision, ...)."""

    kind: str
    resource: str
    at: UtcDatetime
    data: dict[str, Any] = Field(default_factory=dict)
