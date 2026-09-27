"""Observations: the step journal and projections are rebuilt from them.

They leave the core with every transition instead of accumulating in the state.
Values (inputs, outputs) are included as is; the host decides whether to move large
ones into the blob store before writing the journal.
"""

from typing import Annotated, Any, Literal, Union

from pydantic import Field

from ai.gena.services.fabula.engine.model.base import Frozen, UtcDatetime
from ai.gena.services.fabula.engine.model.jsonvalue import Json
from ai.gena.services.fabula.engine.model.problem import Problem
from ai.gena.services.fabula.engine.model.swap import SwapReport

Status = Literal["running", "waiting", "paused", "completed", "failed", "cancelled"]
FINAL_STATUSES = ("completed", "cancelled")

WaitKind = Literal["invocation", "event", "timer", "retry", "deadline", "schedule", "yield"]


class _Observation(Frozen):
    at: UtcDatetime


class FabulaStarted(_Observation):
    kind: Literal["fabula_started"] = "fabula_started"
    scenario_ref: str
    input: Json = None


class NodeStarted(_Observation):
    kind: Literal["node_started"] = "node_started"
    node: str
    task: str
    visit: int
    attempt: int
    input: Json = None


class NodeCompleted(_Observation):
    kind: Literal["node_completed"] = "node_completed"
    node: str
    visit: int
    attempt: int
    output: Json = None


class NodeFailed(_Observation):
    kind: Literal["node_failed"] = "node_failed"
    node: str
    visit: int
    attempt: int
    problem: Problem


class NodeSkipped(_Observation):
    kind: Literal["node_skipped"] = "node_skipped"
    node: str
    visit: int
    reason: Literal["condition", "control"]
    output: Json = None


class NodeCancelled(_Observation):
    kind: Literal["node_cancelled"] = "node_cancelled"
    node: str
    visit: int
    attempt: int
    reason: str


class RetryScheduled(_Observation):
    kind: Literal["retry_scheduled"] = "retry_scheduled"
    node: str
    visit: int
    next_attempt: int
    fire_at: UtcDatetime
    problem: Problem


class WaitInfo(Frozen):
    node: str
    wait: WaitKind
    op_id: str
    until: UtcDatetime | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class Waiting(_Observation):
    kind: Literal["waiting"] = "waiting"
    waits: tuple[WaitInfo, ...]


class ControlAccepted(_Observation):
    kind: Literal["control_accepted"] = "control_accepted"
    request_id: str
    actor: str
    reason: str
    action: str


class ControlRejected(_Observation):
    kind: Literal["control_rejected"] = "control_rejected"
    request_id: str
    actor: str
    reason: str
    action: str
    code: str
    message: str
    report: SwapReport | None = None


IgnoreReason = Literal["duplicate", "late", "filter_mismatch", "already_satisfied", "paused_backlog_full", "final"]


class StimulusIgnored(_Observation):
    kind: Literal["stimulus_ignored"] = "stimulus_ignored"
    stimulus_id: str
    stimulus: str
    reason: IgnoreReason
    detail: str = ""


class StimulusQueued(_Observation):
    kind: Literal["stimulus_queued"] = "stimulus_queued"
    stimulus_id: str
    stimulus: str


class StatusChanged(_Observation):
    kind: Literal["status_changed"] = "status_changed"
    previous: Status
    status: Status


class SafePoint(_Observation):
    """The state after this transition is at rest and may be offloaded."""

    kind: Literal["safe_point"] = "safe_point"
    status: Status


class FabulaCompleted(_Observation):
    kind: Literal["fabula_completed"] = "fabula_completed"
    output: Json = None


class FabulaFailed(_Observation):
    kind: Literal["fabula_failed"] = "fabula_failed"
    node: str | None
    problem: Problem


class FabulaCancelled(_Observation):
    kind: Literal["fabula_cancelled"] = "fabula_cancelled"


class ScenarioSwapped(_Observation):
    kind: Literal["scenario_swapped"] = "scenario_swapped"
    previous_ref: str
    ref: str
    report: SwapReport


Observation = Annotated[
    Union[
        FabulaStarted,
        NodeStarted,
        NodeCompleted,
        NodeFailed,
        NodeSkipped,
        NodeCancelled,
        RetryScheduled,
        Waiting,
        ControlAccepted,
        ControlRejected,
        StimulusIgnored,
        StimulusQueued,
        StatusChanged,
        SafePoint,
        FabulaCompleted,
        FabulaFailed,
        FabulaCancelled,
        ScenarioSwapped,
    ],
    Field(discriminator="kind"),
]
