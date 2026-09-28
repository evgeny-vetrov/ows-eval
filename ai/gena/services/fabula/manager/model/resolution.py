"""Resolving migration conflicts: who may decide, within which limits, and the task
an agent works on when the policy lets agents resolve conflicts."""

from typing import Any, Literal

from pydantic import Field, field_validator

from ai.gena.services.fabula.engine.model.base import Frozen, UtcDatetime
from ai.gena.services.fabula.engine.model.observations import WaitInfo
from ai.gena.services.fabula.engine.model.swap import CursorMove, SwapReport
from ai.gena.services.fabula.manager.model.common import RequestBase

ResolutionMode = Literal["manual", "agent_proposes", "agent_applies"]
ResolutionAction = Literal["resolve", "stay", "cancel_fabula"]


class ResolutionPolicy(Frozen):
    """Set per scenario; a migration campaign may override it.

    * `manual` — only people decide; delegating to an agent is refused.
    * `agent_proposes` — an agent proposes, a person approves.
    * `agent_applies` — a proposal that passes the dry run and every limit below is
      applied without a person.
    """

    mode: ResolutionMode = "manual"
    allowed_actions: tuple[ResolutionAction, ...] = ("resolve", "stay")
    # Only swaps that keep every frame and operation (no restarts).
    require_compatible: bool = False
    # Never cancel an in-flight invocation of a capability with effects other than
    # "none" (unknown effects count as effectful).
    forbid_cancelling_effectful: bool = True
    max_items: int = Field(default=100, ge=1, le=10_000)
    # Actor ids allowed to work on tasks; empty means any actor of kind "agent".
    allowed_actors: tuple[str, ...] = ()
    # Whether the brief carries context values or only their keys.
    expose_context_values: bool = False


class ProposedResolution(Frozen):
    """An answer to a resolution task. `fabula_ids` narrows it to part of the task."""

    action: ResolutionAction
    mapping: dict[str, str] = Field(default_factory=dict)
    context_migration: str | None = None
    fabula_ids: tuple[str, ...] = ()
    rationale: str = ""
    confidence: float | None = Field(default=None, ge=0, le=1)


class Violation(Frozen):
    code: str
    message: str
    fabula_id: str | None = None


class DryRunItem(Frozen):
    fabula_id: str
    verdict: str
    report: SwapReport | None = None
    violations: tuple[Violation, ...] = ()


class DryRunResult(Frozen):
    acceptable: bool
    items: tuple[DryRunItem, ...] = ()
    violations: tuple[Violation, ...] = ()


TaskStatus = Literal["open", "leased", "awaiting_approval", "applying", "applied", "rejected", "closed"]


class AgenticResolutionTask(Frozen):
    """A group of blocked migration items handed to an agent (one cluster per task)."""

    task_id: str
    campaign_id: str
    cluster_id: str
    fabula_ids: tuple[str, ...]
    pool: str = "default"
    status: TaskStatus = "open"
    lease_holder: str | None = None
    lease_expires_at: UtcDatetime | None = None
    proposal: ProposedResolution | None = None
    proposed_by: str | None = None
    dry_run: DryRunResult | None = None
    decided_by: str | None = None
    note: str = ""
    attempts: int = 0
    created_at: UtcDatetime
    updated_at: UtcDatetime
    revision: int = 1


class RepresentativeFabula(Frozen):
    fabula_id: str
    from_ref: str
    status: str
    cursor: tuple[CursorMove, ...] = ()
    waits: tuple[WaitInfo, ...] = ()
    context_keys: tuple[str, ...] = ()
    context: Any = None


class ResolutionBrief(Frozen):
    """Everything an agent needs to propose a resolution, and nothing it may not see."""

    task_id: str
    campaign_id: str
    scenario: str
    target_ref: str
    fabula_ids: tuple[str, ...]
    representative: RepresentativeFabula
    problems: tuple[str, ...] = ()
    missing_variables: tuple[str, ...] = ()
    current_mapping: dict[str, str] = Field(default_factory=dict)
    suggested_mapping: dict[str, str] = Field(default_factory=dict)
    changed_nodes: tuple[str, ...] = ()
    old_sources: dict[str, Any] = Field(default_factory=dict)
    new_sources: dict[str, Any] = Field(default_factory=dict)
    target_nodes: tuple[str, ...] = ()
    allowed_actions: tuple[ResolutionAction, ...] = ()
    policy: ResolutionPolicy
    proposal_schema: dict[str, Any] = Field(default_factory=dict)


# --- requests ------------------------------------------------------------------------


class LeaseTasks(RequestBase):
    pool: str = "default"
    max_tasks: int = Field(default=1, ge=1, le=100)
    lease_seconds: int = Field(default=300, ge=10, le=86_400)


class LeasedTask(Frozen):
    task: AgenticResolutionTask
    brief: ResolutionBrief


class LeasedTasks(Frozen):
    items: tuple[LeasedTask, ...]


class TaskList(Frozen):
    items: tuple[AgenticResolutionTask, ...]


class SubmitProposal(RequestBase):
    proposal: ProposedResolution


class DryRunProposal(Frozen):
    proposal: ProposedResolution


class ReviewProposal(RequestBase):
    reason: str = ""
    # For a rejection: give the task back to the pool (true) or close it (false).
    reopen: bool = True


class SetResolutionPolicy(RequestBase):
    policy: ResolutionPolicy
    expected_revision: int | None = None

    @field_validator("expected_revision")
    @classmethod
    def _positive(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("expected_revision starts at 1")
        return value
