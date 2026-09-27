"""Commands: what the core asks the host to do. A closed tagged union on `kind`."""

from datetime import timedelta
from typing import Annotated, Literal, Union

from pydantic import Field

from ai.gena.services.fabula.engine.model.base import Frozen, UtcDatetime
from ai.gena.services.fabula.engine.model.context import ExecutionContext
from ai.gena.services.fabula.engine.model.event import Event
from ai.gena.services.fabula.engine.model.filter import Filter
from ai.gena.services.fabula.engine.model.jsonvalue import Json
from ai.gena.services.fabula.engine.model.problem import Problem


class InvokeCapability(Frozen):
    kind: Literal["invoke_capability"] = "invoke_capability"
    invocation_id: str
    capability_ref: str
    input: Json = None
    timeout: timedelta | None = None
    context: ExecutionContext = ExecutionContext()


class CancelInvocation(Frozen):
    kind: Literal["cancel_invocation"] = "cancel_invocation"
    invocation_id: str


class StartTimer(Frozen):
    kind: Literal["start_timer"] = "start_timer"
    timer_id: str
    fire_at: UtcDatetime


class CancelTimer(Frozen):
    kind: Literal["cancel_timer"] = "cancel_timer"
    timer_id: str


class Subscribe(Frozen):
    kind: Literal["subscribe"] = "subscribe"
    subscription_id: str
    event_types: tuple[str, ...]
    filter: Filter
    since: UtcDatetime
    expires_at: UtcDatetime | None = None
    context: ExecutionContext = ExecutionContext()


class Unsubscribe(Frozen):
    kind: Literal["unsubscribe"] = "unsubscribe"
    subscription_id: str


class PublishEvent(Frozen):
    kind: Literal["publish_event"] = "publish_event"
    emit_id: str
    event: Event


class Completed(Frozen):
    kind: Literal["completed"] = "completed"
    output: Json = None


class Failed(Frozen):
    kind: Literal["failed"] = "failed"
    problem: Problem


class Cancelled(Frozen):
    kind: Literal["cancelled"] = "cancelled"


Command = Annotated[
    Union[
        InvokeCapability,
        CancelInvocation,
        StartTimer,
        CancelTimer,
        Subscribe,
        Unsubscribe,
        PublishEvent,
        Completed,
        Failed,
        Cancelled,
    ],
    Field(discriminator="kind"),
]
