from typing import Annotated, Literal, Union

from pydantic import Field, field_validator, model_validator

from ai.gena.services.fabula.engine.core.journal import StepRecord
from ai.gena.services.fabula.engine.core.projection import FabulaView
from ai.gena.services.fabula.engine.model.base import Frozen, UtcDatetime
from ai.gena.services.fabula.engine.model.jsonvalue import Json
from ai.gena.services.fabula.engine.model.observations import Observation, WaitInfo
from ai.gena.services.fabula.engine.model.problem import Problem
from ai.gena.services.fabula.engine.model.stimuli import (
    Cancel,
    CompleteWait,
    ExtendDeadline,
    Goto,
    PatchContext,
    Pause,
    Resume,
    RetryTask,
    SkipTask,
)
from ai.gena.services.fabula.engine.model.swap import SwapReport
from ai.gena.services.fabula.manager.model.common import RequestBase
from ai.gena.services.fabula.manager.model.tags import Assignment, check_tag

RESERVED_LABEL_PREFIX = "fabula."

RecordStatus = Literal["starting", "start_failed", "running", "waiting", "paused", "completed", "failed", "cancelled"]
ACTIVE_STATUSES: tuple[RecordStatus, ...] = ("running", "waiting", "paused", "failed")
Origin = Literal["manager", "external", "imported"]


class SwapRecord(Frozen):
    at: UtcDatetime
    from_ref: str
    to_ref: str


class FabulaRecord(Frozen):
    """Read model of one fabula, projected from its journal. May lag the state; the
    `runtime` stays authoritative for decisions."""

    fabula_id: str
    scenario: str
    ref: str
    version: str
    status: RecordStatus
    at_rest: bool = False
    waits: tuple[WaitInfo, ...] = ()
    last_error: Problem | None = None
    failed_node: str | None = None
    started_at: UtcDatetime | None = None
    registered_at: UtcDatetime
    updated_at: UtcDatetime
    assignment: Assignment | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    run_as: str | None = None
    origin: Origin = "manager"
    request_id: str | None = None
    request_fingerprint: str | None = None
    swaps: tuple[SwapRecord, ...] = ()
    # The last journal version applied; versions are applied strictly in order.
    journal_version: int = 0
    # The index missed a journal version or failed to apply one; `reconcile` repairs it.
    stale: bool = False
    revision: int = 1


OrderBy = Literal["started_at", "updated_at"]


class FabulaQuery(Frozen):
    scenario: str | None = None
    versions: tuple[str, ...] = ()
    statuses: tuple[RecordStatus, ...] = ()
    tag: str | None = None
    arm: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    waiting_on: str | None = None
    started_after: UtcDatetime | None = None
    started_before: UtcDatetime | None = None
    stale: bool | None = None
    fabula_ids: tuple[str, ...] = ()
    order_by: OrderBy = "started_at"
    page_size: int = Field(default=50, ge=1, le=1000)
    page_token: str | None = None


class FabulaPage(Frozen):
    items: tuple[FabulaRecord, ...]
    next_page_token: str | None = None


class CountRow(Frozen):
    scenario: str
    version: str
    status: RecordStatus
    count: int


class Counts(Frozen):
    items: tuple[CountRow, ...]


class JournalEntry(Frozen):
    version: int
    observations: tuple[Observation, ...] = ()


class Selector(Frozen):
    """Which version to start: through a tag (default `trunk`) or an explicit version."""

    tag: str | None = None
    version: str | None = None

    @model_validator(mode="after")
    def _at_most_one(self) -> "Selector":
        if self.tag is not None and self.version is not None:
            raise ValueError("give a tag or a version, not both")
        if self.tag is not None:
            check_tag(self.tag)
        return self


class StartFabula(RequestBase):
    scenario: str
    selector: Selector = Selector()
    input: Json = None
    # Key for A/B routing; defaults to the fabula id.
    routing_key: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    run_as: str | None = None
    trigger: Json = None
    # Defaults to a hash of the scenario and the request id, so retries start nothing new.
    fabula_id: str | None = None

    @field_validator("labels")
    @classmethod
    def _labels(cls, value: dict[str, str]) -> dict[str, str]:
        reserved = sorted(key for key in value if key.startswith(RESERVED_LABEL_PREFIX))
        if reserved:
            raise ValueError(f"label prefix '{RESERVED_LABEL_PREFIX}' is reserved: {', '.join(reserved)}")
        return value


class StartResult(Frozen):
    fabula_id: str
    ref: str
    assignment: Assignment
    created: bool


class FabulaDetails(Frozen):
    record: FabulaRecord
    # Authoritative projection of the stored state; absent if the runtime does not know it.
    view: FabulaView | None = None


class Journal(Frozen):
    fabula_id: str
    steps: tuple[StepRecord, ...]


ManagerControlAction = Annotated[
    Union[Pause, Resume, Cancel, SkipTask, RetryTask, Goto, PatchContext, CompleteWait, ExtendDeadline],
    Field(discriminator="type"),
]


class ControlFabula(RequestBase):
    """An intervention. Version swaps go through migration campaigns instead."""

    action: ManagerControlAction
    reason: str = ""


class ControlOutcome(Frozen):
    """What happened to a control request, recorded from the fabula's journal."""

    fabula_id: str
    request_id: str
    action: str
    accepted: bool
    code: str = ""
    message: str = ""
    actor: str = ""
    at: UtcDatetime
    journal_version: int
    report: SwapReport | None = None
    swapped_to: str | None = None


class ControlAck(Frozen):
    fabula_id: str
    request_id: str
    # Empty while the runtime has not applied the request yet.
    outcome: ControlOutcome | None = None


class AnalyzeSwap(Frozen):
    version: str
    mapping: dict[str, str] = Field(default_factory=dict)
    context_migration: str | None = None


def reserved_labels(assignment: Assignment, request_id: str) -> dict[str, str]:
    labels = {"fabula.version": assignment.version, "fabula.request": request_id}
    if assignment.tag is not None:
        labels.update(
            {
                "fabula.tag": assignment.tag,
                "fabula.tag_revision": str(assignment.tag_revision),
                "fabula.arm": str(assignment.arm),
                "fabula.bucket": str(assignment.bucket),
            }
        )
    return labels
