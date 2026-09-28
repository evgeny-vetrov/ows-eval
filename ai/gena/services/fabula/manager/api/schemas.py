"""Models that exist only at the API boundary: query strings, lists and problems."""

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ai.gena.services.fabula.engine.model.base import Frozen, UtcDatetime
from ai.gena.services.fabula.engine.model.catalog import CapabilityDescriptor, EventTypeDescriptor
from ai.gena.services.fabula.manager.model.campaigns import CampaignStatus, ItemState, Verdict
from ai.gena.services.fabula.manager.model.common import RequestBase
from ai.gena.services.fabula.manager.model.fabulas import FabulaQuery, OrderBy, RecordStatus
from ai.gena.services.fabula.manager.model.resolution import TaskStatus


class ProblemDetails(BaseModel):
    """RFC 7807 problem. `code` is the stable machine-readable reason; other members
    (diagnostics, dry_run, errors...) depend on the code."""

    model_config = ConfigDict(extra="allow")

    type: str
    title: str
    status: int
    detail: str = ""
    code: str


@dataclass(frozen=True)
class ApiResult:
    """A handler's value with a status other than the operation's default."""

    status: int
    value: Any


# --- query strings -----------------------------------------------------------------


class NamespaceQuery(Frozen):
    namespace: str | None = None


class CatalogQuery(Frozen):
    q: str | None = None


class DiffQuery(Frozen):
    from_version: str
    to_version: str


class RoutingQuery(Frozen):
    routing_key: str


class FabulaSearch(Frozen):
    scenario: str | None = None
    version: tuple[str, ...] = ()
    status: tuple[RecordStatus, ...] = ()
    tag: str | None = None
    arm: str | None = None
    # `key=value` pairs; all must match.
    label: tuple[str, ...] = ()
    waiting_on: str | None = None
    started_after: UtcDatetime | None = None
    started_before: UtcDatetime | None = None
    stale: bool | None = None
    order_by: OrderBy = "started_at"
    page_size: int = Field(default=50, ge=1, le=1000)
    page_token: str | None = None

    def to_query(self) -> FabulaQuery:
        labels = {}
        for pair in self.label:
            key, sep, value = pair.partition("=")
            if not sep:
                raise ValueError(f"label '{pair}' is not key=value")
            labels[key] = value
        return FabulaQuery(
            scenario=self.scenario,
            versions=self.version,
            statuses=self.status,
            tag=self.tag,
            arm=self.arm,
            labels=labels,
            waiting_on=self.waiting_on,
            started_after=self.started_after,
            started_before=self.started_before,
            stale=self.stale,
            order_by=self.order_by,
            page_size=self.page_size,
            page_token=self.page_token,
        )


class ScenarioFilter(Frozen):
    scenario: str | None = None


class MigrationQuery(Frozen):
    scenario: str | None = None
    status: CampaignStatus | None = None


class ItemQuery(Frozen):
    verdict: Verdict | None = None
    state: ItemState | None = None
    cluster_id: str | None = None
    page_size: int = Field(default=100, ge=1, le=1000)
    page_token: str | None = None


class TaskQuery(Frozen):
    campaign_id: str | None = None
    status: TaskStatus | None = None
    pool: str | None = None


class AuditQuery(Frozen):
    resource: str | None = None
    limit: int = Field(default=100, ge=1, le=1000)


# --- bodies and results ------------------------------------------------------------


class ImportFabula(RequestBase):
    pass


class ReconcileResult(Frozen):
    repaired: int


class CapabilityList(Frozen):
    items: tuple[CapabilityDescriptor, ...]


class EventTypeList(Frozen):
    items: tuple[EventTypeDescriptor, ...]
