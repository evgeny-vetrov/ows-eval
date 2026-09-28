"""Stimuli: everything that can happen to a fabula. A closed tagged union on `kind`."""

from typing import Annotated, Literal, Union

from pydantic import Field

from ai.gena.services.fabula.engine.model.base import Frozen, UtcDatetime
from ai.gena.services.fabula.engine.model.event import Event
from ai.gena.services.fabula.engine.model.jsonvalue import Json
from ai.gena.services.fabula.engine.model.problem import Problem
from ai.gena.services.fabula.engine.model.scenario import ScenarioSnapshot


class OutcomeOk(Frozen):
    status: Literal["ok"] = "ok"
    output: Json = None


class OutcomeError(Frozen):
    status: Literal["error"] = "error"
    problem: Problem


class OutcomeCancelled(Frozen):
    status: Literal["cancelled"] = "cancelled"


Outcome = Annotated[Union[OutcomeOk, OutcomeError, OutcomeCancelled], Field(discriminator="status")]


class Value(Frozen):
    """Wraps an optional JSON value so that `null` stays distinguishable from "not given"."""

    value: Json = None


class Pause(Frozen):
    type: Literal["pause"] = "pause"


class Resume(Frozen):
    type: Literal["resume"] = "resume"


class Cancel(Frozen):
    type: Literal["cancel"] = "cancel"


class SkipTask(Frozen):
    type: Literal["skip_task"] = "skip_task"
    node: str
    output: Json = None


class RetryTask(Frozen):
    type: Literal["retry_task"] = "retry_task"
    node: str


class Goto(Frozen):
    type: Literal["goto"] = "goto"
    node: str
    input: Value | None = None
    allow_scope_change: bool = False


class PatchContext(Frozen):
    type: Literal["patch_context"] = "patch_context"
    patch: Json


class CompleteWait(Frozen):
    type: Literal["complete_wait"] = "complete_wait"
    node: str
    event: Event | None = None
    output: Value | None = None


class ExtendDeadline(Frozen):
    type: Literal["extend_deadline"] = "extend_deadline"
    node: str
    fire_at: UtcDatetime


class SwapVersion(Frozen):
    type: Literal["swap_version"] = "swap_version"
    scenario: ScenarioSnapshot
    mapping: dict[str, str] = Field(default_factory=dict)
    context_migration: str | None = None


ControlAction = Annotated[
    Union[Pause, Resume, Cancel, SkipTask, RetryTask, Goto, PatchContext, CompleteWait, ExtendDeadline, SwapVersion],
    Field(discriminator="type"),
]


class _Stimulus(Frozen):
    stimulus_id: str
    at: UtcDatetime


class InvocationFinished(_Stimulus):
    kind: Literal["invocation_finished"] = "invocation_finished"
    invocation_id: str
    outcome: Outcome


class TimerFired(_Stimulus):
    kind: Literal["timer_fired"] = "timer_fired"
    timer_id: str


class EventDelivered(_Stimulus):
    kind: Literal["event_delivered"] = "event_delivered"
    subscription_id: str
    delivery_key: str
    event: Event


class Control(_Stimulus):
    """Intervention by a person or a system. The host checks the actor's rights."""

    kind: Literal["control"] = "control"
    request_id: str
    actor: str
    reason: str = ""
    action: ControlAction


Stimulus = Annotated[Union[InvocationFinished, TimerFired, EventDelivered, Control], Field(discriminator="kind")]
