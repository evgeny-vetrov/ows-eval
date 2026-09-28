"""Requests and results of the sandbox API."""

from datetime import timedelta
from typing import Literal

from pydantic import Field, field_validator

from ai.gena.services.fabula.engine.model.base import Frozen, UtcDatetime
from ai.gena.services.fabula.engine.model.event import Event
from ai.gena.services.fabula.engine.model.jsonvalue import Json
from ai.gena.services.fabula.engine.model.stimuli import Outcome

InvocationState = Literal["pending", "finished", "cancelled"]


class Invocation(Frozen):
    """A capability call a fabula made. `pending` ones wait for
    `POST /v1/sandbox/invocations:complete` (or a configured behaviour)."""

    invocation_id: str
    fabula_id: str
    capability_ref: str
    input: Json = None
    state: InvocationState
    outcome: Outcome | None = None


class InvocationQuery(Frozen):
    state: InvocationState | None = None
    fabula_id: str | None = None
    capability: str | None = None


class InvocationList(Frozen):
    items: tuple[Invocation, ...]


class CompleteInvocation(Frozen):
    """Idempotent by `invocation_id`: the same outcome again is harmless, another one
    is a conflict. A cancelled invocation may still complete: the engine ignores it,
    as it would a late result from a real executor."""

    invocation_id: str
    outcome: Outcome


class PublishEvent(Frozen):
    """An event from the outside world; fabulas whose subscriptions match receive it."""

    type: str
    source: str = "sandbox"
    subject: str | None = None
    data: Json = None
    attributes: dict[str, Json] = Field(default_factory=dict)
    # Defaults to a fresh id; repeating an id publishes a duplicate, which is harmless.
    id: str | None = None


class SandboxEvent(Frozen):
    at: UtcDatetime
    # `fabula`: published by a fabula's `emit`; `external`: through the sandbox API.
    origin: Literal["fabula", "external"]
    event: Event


class EventQuery(Frozen):
    type: str | None = None
    limit: int = Field(default=100, ge=1, le=1000)


class SandboxEventList(Frozen):
    items: tuple[SandboxEvent, ...]


class AdvanceClock(Frozen):
    """Move the sandbox clock forward (ISO 8601 duration such as `PT1H`, or seconds);
    timers that become due fire."""

    by: timedelta

    @field_validator("by")
    @classmethod
    def _forward(cls, value: timedelta) -> timedelta:
        if value <= timedelta(0) or value > timedelta(days=366):
            raise ValueError("the clock moves forward by up to a year")
        return value


class Timer(Frozen):
    timer_id: str
    fabula_id: str
    fire_at: UtcDatetime


class DeadLetter(Frozen):
    """A result, event or firing the host kept failing on; it was set aside."""

    fabula_id: str
    stimulus_id: str
    stimulus_kind: str
    error: str
    at: UtcDatetime


class SettleResult(Frozen):
    settled: bool
    pending_deliveries: int
    dead_letters: int


class SandboxStatus(Frozen):
    now: UtcDatetime
    clock_offset: timedelta
    pending_deliveries: int
    invocations: dict[InvocationState, int]
    timers: tuple[Timer, ...]
    active_subscriptions: int
    dead_letters: tuple[DeadLetter, ...]
