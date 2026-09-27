"""Persisted fabula state.

Every model here is frozen; the interpreter replaces frames instead of mutating them, so
a transition never alters the state it was given and JSON values can be shared.
"""

from typing import Annotated, Literal, Union

from pydantic import Field

from ai.gena.services.fabula.engine.core.semantics import Semantics
from ai.gena.services.fabula.engine.model.base import Frozen, UtcDatetime
from ai.gena.services.fabula.engine.model.commands import Subscribe
from ai.gena.services.fabula.engine.model.context import ExecutionContext
from ai.gena.services.fabula.engine.model.jsonvalue import Json
from ai.gena.services.fabula.engine.model.observations import Status
from ai.gena.services.fabula.engine.model.problem import Problem
from ai.gena.services.fabula.engine.model.scenario import ScenarioSnapshot
from ai.gena.services.fabula.engine.model.stimuli import Stimulus
from ai.gena.services.fabula.engine.version import ENGINE_VERSION, STATE_SCHEMA_VERSION

OpType = Literal["invocation", "timer", "subscription"]
OpRole = Literal[
    "call", "timeout", "wait", "retry", "attempt_timeout", "sub", "until", "yield", "schedule", "workflow_timeout"
]


class InflightOp(Frozen):
    op_id: str
    op: OpType
    role: OpRole
    owner: str
    fire_at: UtcDatetime | None = None
    capability_ref: str | None = None
    cancellable: bool = True
    index: int | None = None
    subscription: Subscribe | None = None
    delivery_keys: tuple[str, ...] = ()


class _Frame(Frozen):
    path: str
    visit: int
    attempt: int = 1
    raw_input: Json = None
    input: Json = None
    started_at: UtcDatetime
    timeout_op: str | None = None
    failed: Problem | None = None


class WorkflowFrame(_Frame):
    kind: Literal["workflow"] = "workflow"
    current: str | None = None


class DoFrame(_Frame):
    kind: Literal["do"] = "do"
    current: str | None = None


class TryFrame(_Frame):
    kind: Literal["try"] = "try"
    phase: Literal["try", "retry_wait", "catch"] = "try"
    current: str | None = None
    error: Problem | None = None
    retries: int = 0
    first_started_at: UtcDatetime
    attempt_timer: str | None = None
    retry_timer: str | None = None


class ForFrame(_Frame):
    kind: Literal["for"] = "for"
    items: tuple[Json, ...] = ()
    index: int = 0
    acc: Json = None
    current: str | None = None


class Branch(Frozen):
    path: str
    status: Literal["running", "completed", "failed", "cancelled"] = "running"
    output: Json = None


class ForkFrame(_Frame):
    kind: Literal["fork"] = "fork"
    branches: tuple[Branch, ...] = ()


class CallFrame(_Frame):
    kind: Literal["call"] = "call"
    invocation_id: str | None = None
    arguments: Json = None
    retries: int = 0
    retry_timer: str | None = None


class WaitFrame(_Frame):
    kind: Literal["wait"] = "wait"
    timer_op: str | None = None
    fire_at: UtcDatetime | None = None


class ListenFrame(_Frame):
    kind: Literal["listen"] = "listen"
    since: UtcDatetime
    main_subs: tuple[str | None, ...] = ()
    until_subs: tuple[str | None, ...] = ()
    until_hits: tuple[bool, ...] = ()
    events: tuple[Json, ...] = ()
    by_filter: tuple[Json, ...] = ()
    satisfied: tuple[bool, ...] = ()
    buffer: tuple[Json, ...] = ()
    processing: bool = False
    closing: bool = False
    item: Json = None
    item_index: int = 0
    acc: Json = None
    current: str | None = None


class LeafFrame(_Frame):
    kind: Literal["leaf"] = "leaf"


Frame = Annotated[
    Union[WorkflowFrame, DoFrame, TryFrame, ForFrame, ForkFrame, CallFrame, WaitFrame, ListenFrame, LeafFrame],
    Field(discriminator="kind"),
]


class Enter(Frozen):
    item: Literal["enter"] = "enter"
    path: str
    raw_input: Json = None
    visit: int | None = None
    attempt: int = 1
    # Set by controls: the node runs even if its `if` is false.
    forced: bool = False
    # Goto into nested constructible containers: children to enter on the way down.
    descend: tuple[str, ...] = ()


class Finish(Frozen):
    item: Literal["finish"] = "finish"
    path: str
    raw_output: Json = None
    then: str | None = None


class Fail(Frozen):
    item: Literal["fail"] = "fail"
    path: str
    problem: Problem


AgendaItem = Annotated[Union[Enter, Finish, Fail], Field(discriminator="item")]


class ControlRecord(Frozen):
    request_id: str
    accepted: bool
    code: str = ""


class FabulaState(Frozen):
    schema_version: int = STATE_SCHEMA_VERSION
    engine_version: str = ENGINE_VERSION
    semantics: Semantics
    fabula_id: str
    status: Status
    scenario: ScenarioSnapshot
    execution_context: ExecutionContext
    workflow_input: Json = None
    data: Json = Field(default_factory=dict)
    started_at: UtcDatetime
    last_at: UtcDatetime
    frames: dict[str, Frame] = Field(default_factory=dict)
    inflight: dict[str, InflightOp] = Field(default_factory=dict)
    visits: dict[str, int] = Field(default_factory=dict)
    agenda: tuple[AgendaItem, ...] = ()
    paused_queue: tuple[Stimulus, ...] = ()
    recent_stimuli: tuple[str, ...] = ()
    recent_controls: tuple[ControlRecord, ...] = ()
    yield_seq: int = 0
    output: Json = None
    problem: Problem | None = None
    failed_node: str | None = None
    last_error: Problem | None = None
