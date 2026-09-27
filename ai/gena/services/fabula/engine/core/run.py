"""One transition: the mutable working copy of a state plus what the transition emits."""

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ai.gena.services.fabula.engine.core import ids
from ai.gena.services.fabula.engine.core.state import FabulaState, InflightOp
from ai.gena.services.fabula.engine.dsl.graph import ROOT, Graph
from ai.gena.services.fabula.engine.model.commands import (
    CancelInvocation,
    CancelTimer,
    Command,
    InvokeCapability,
    StartTimer,
    Subscribe,
    Unsubscribe,
)
from ai.gena.services.fabula.engine.model.observations import (
    NodeCancelled,
    Observation,
    SafePoint,
    StatusChanged,
    StimulusIgnored,
    Waiting,
    WaitInfo,
)

ACTIVE = ("running", "waiting")
TERMINAL = ("completed", "failed", "cancelled")

_WAIT_KIND = {
    "wait": "timer",
    "retry": "retry",
    "timeout": "deadline",
    "attempt_timeout": "deadline",
    "workflow_timeout": "deadline",
    "schedule": "schedule",
    "yield": "yield",
}


@dataclass(frozen=True)
class Transition:
    state: FabulaState
    commands: tuple[Command, ...]
    observations: tuple[Observation, ...]


def time_arg(value: datetime) -> dict[str, Any]:
    millis = int(value.timestamp() * 1000)
    return {"iso8601": value.isoformat().replace("+00:00", "Z"), "epoch": {"seconds": millis // 1000, "milliseconds": millis}}


def is_under(path: str, scope: str) -> bool:
    return scope == ROOT or path == scope or path.startswith(scope + "/")


def is_strictly_under(path: str, scope: str) -> bool:
    return path != scope and is_under(path, scope)


def waits_of(inflight: dict[str, InflightOp]) -> tuple[WaitInfo, ...]:
    waits = []
    for op in inflight.values():
        if op.op == "invocation":
            waits.append(WaitInfo(node=op.owner, wait="invocation", op_id=op.op_id, detail={"capability": op.capability_ref}))
        elif op.op == "subscription":
            waits.append(
                WaitInfo(node=op.owner, wait="event", op_id=op.op_id, until=op.subscription.expires_at, detail={"event_types": list(op.subscription.event_types)})
            )
        else:
            waits.append(WaitInfo(node=op.owner, wait=_WAIT_KIND[op.role], op_id=op.op_id, until=op.fire_at))
    return tuple(waits)


class RunBase:
    def __init__(self, interpreter, graph: Graph, state: FabulaState, at: datetime):
        self.interpreter = interpreter
        self.expressions = interpreter.expressions
        self.matcher = interpreter.matcher
        self.schemas = interpreter.schemas
        self.limits = interpreter.limits
        self.graph = graph
        self.state0 = state
        self.at = max(at, state.last_at)
        self.fabula_id = state.fabula_id
        self.semantics = state.semantics
        self.scenario = state.scenario
        self.status = state.status
        self.frames = dict(state.frames)
        self.inflight = dict(state.inflight)
        self.visits = dict(state.visits)
        self.agenda = deque(state.agenda)
        self.paused_queue = list(state.paused_queue)
        self.recent_stimuli = list(state.recent_stimuli)
        self.recent_controls = list(state.recent_controls)
        self.data = state.data
        self.output = state.output
        self.problem = state.problem
        self.failed_node = state.failed_node
        self.last_error = state.last_error
        self.yield_seq = state.yield_seq
        self.commands: list[Command] = []
        self.observations: list[Observation] = []
        self.steps = 0
        self._workflow_arg = None

    # --- recording ---------------------------------------------------------------

    def command(self, command: Command) -> None:
        self.commands.append(command)

    def observe(self, observation: Observation) -> None:
        self.observations.append(observation)

    def ignore(self, stimulus, reason: str, detail: str = "") -> None:
        self.observe(StimulusIgnored(at=self.at, stimulus_id=stimulus.stimulus_id, stimulus=stimulus.kind, reason=reason, detail=detail))

    # --- frames ------------------------------------------------------------------

    def put(self, frame):
        self.frames[frame.path] = frame
        return frame

    def update(self, path: str, **changes):
        frame = self.frames[path].model_copy(update=changes)
        self.frames[path] = frame
        return frame

    def next_visit(self, path: str) -> int:
        visit = self.visits.get(path, 0) + 1
        self.visits[path] = visit
        return visit

    def remove_frames_under(self, scope: str, reason: str, include_self: bool, keep_failed: bool = False) -> None:
        for path in [p for p in self.frames if is_under(p, scope) and (include_self or p != scope)]:
            frame = self.frames[path]
            if keep_failed and frame.failed is not None:
                continue
            del self.frames[path]
            # A failed frame already has its NodeFailed; it is not cancelled on top of that.
            if path != ROOT and frame.failed is None:
                self.observe(NodeCancelled.model_construct(at=self.at, node=path, visit=frame.visit, attempt=frame.attempt, reason=reason))

    # --- operations ----------------------------------------------------------------

    def start_timer(self, op_id: str, role: str, owner: str, fire_at: datetime, index: int | None = None) -> None:
        self.inflight[op_id] = InflightOp(op_id=op_id, op="timer", role=role, owner=owner, fire_at=fire_at, index=index)
        self.command(StartTimer(timer_id=op_id, fire_at=fire_at))

    def start_invocation(self, command: InvokeCapability, owner: str, cancellable: bool) -> None:
        self.inflight[command.invocation_id] = InflightOp(
            op_id=command.invocation_id, op="invocation", role="call", owner=owner, capability_ref=command.capability_ref, cancellable=cancellable
        )
        self.command(command)

    def start_subscription(self, command: Subscribe, role: str, owner: str, index: int) -> None:
        self.inflight[command.subscription_id] = InflightOp(
            op_id=command.subscription_id, op="subscription", role=role, owner=owner, index=index, subscription=command
        )
        self.command(command)

    def close_op(self, op_id: str) -> None:
        op = self.inflight.pop(op_id, None)
        if op is None:
            return
        if op.op == "invocation":
            if op.cancellable:
                self.command(CancelInvocation(invocation_id=op_id))
        elif op.op == "timer":
            self.command(CancelTimer(timer_id=op_id))
        else:
            self.command(Unsubscribe(subscription_id=op_id))

    def close_ops_under(self, scope: str) -> None:
        for op_id in [op_id for op_id, op in self.inflight.items() if is_under(op.owner, scope)]:
            self.close_op(op_id)

    def drop_agenda_under(self, scope: str, include_self: bool) -> None:
        self.agenda = deque(item for item in self.agenda if not (is_under(item.path, scope) and (include_self or item.path != scope)))

    def drop(self, scope: str, reason: str) -> None:
        """Leave a scope: close everything it holds, forget its frames and pending work."""
        self.close_ops_under(scope)
        self.remove_frames_under(scope, reason, include_self=True)
        self.drop_agenda_under(scope, include_self=True)

    def clear_inside(self, scope: str, reason: str) -> None:
        """Like `drop` for everything below `scope`; the scope keeps its own deadline."""
        for op_id in [op_id for op_id, op in self.inflight.items() if is_strictly_under(op.owner, scope)]:
            self.close_op(op_id)
        self.remove_frames_under(scope, reason, include_self=False)
        self.drop_agenda_under(scope, include_self=False)

    # --- expression arguments -------------------------------------------------------

    def workflow_arg(self) -> dict[str, Any]:
        if self._workflow_arg is None:
            self._workflow_arg = {
                "id": self.fabula_id,
                "definition": {"document": self.scenario.document.get("document")},
                "input": self.state0.workflow_input,
                "startedAt": time_arg(self.state0.started_at),
            }
        return self._workflow_arg

    def args(self, path: str, input: Any, **extra: Any) -> dict[str, Any]:
        node = self.graph.nodes[path]
        frame = self.frames.get(path)
        args = {
            "context": self.data,
            "input": input,
            "workflow": self.workflow_arg(),
            "runtime": {"name": "fabula", "version": self.semantics.engine_version, "metadata": {}},
            "task": {
                "name": node.name,
                "reference": node.pointer,
                "input": frame.raw_input if frame else input,
                "startedAt": time_arg(frame.started_at if frame else self.at),
            },
        }
        args.update(self.scope_vars(path))
        args.update(extra)
        return args

    def scope_vars(self, path: str) -> dict[str, Any]:
        found: dict[str, Any] = {}
        for ancestor in self.graph.ancestors(path):
            frame = self.frames.get(ancestor)
            if frame is None:
                continue
            node = self.graph.nodes[ancestor]
            if frame.kind == "for" and frame.index < len(frame.items):
                found[node.for_each] = frame.items[frame.index]
                found[node.for_at] = frame.index
            elif frame.kind == "listen" and node.listen.foreach:
                found[node.listen.item] = frame.item
                found[node.listen.at] = frame.item_index
            elif frame.kind == "try" and frame.phase == "catch" and frame.error is not None:
                found[node.catch.as_name] = frame.error.to_json()
        return found

    # --- agenda --------------------------------------------------------------------

    def push(self, item) -> None:
        self.agenda.append(item)

    def run_agenda(self) -> None:
        while self.agenda and self.status == "running":
            if self.steps >= self.limits.max_steps_per_transition:
                self.yield_control()
                return
            self.steps += 1
            self.dispatch(self.agenda.popleft())

    def yield_control(self) -> None:
        if any(op.role == "yield" for op in self.inflight.values()):
            return
        self.yield_seq += 1
        self.start_timer(ids.workflow_op_id(self.fabula_id, f"yield.{self.yield_seq}"), "yield", ROOT, self.at)

    def activate(self) -> None:
        if self.status in ACTIVE:
            self.status = "running"

    # --- result ----------------------------------------------------------------------

    def remember_stimulus(self, stimulus_id: str) -> None:
        self.recent_stimuli.append(stimulus_id)
        overflow = len(self.recent_stimuli) - self.limits.max_recent_stimuli
        if overflow > 0:
            del self.recent_stimuli[:overflow]

    def finalize(self) -> Transition:
        if self.status in TERMINAL:
            self.agenda.clear()
        elif self.status == "running" and not self.agenda:
            self.status = "waiting"
        if self.status != self.state0.status:
            self.observe(StatusChanged(at=self.at, previous=self.state0.status, status=self.status))
        waits = waits_of(self.inflight)
        if self.status in ("running", "waiting", "paused") and waits != waits_of(self.state0.inflight):
            self.observe(Waiting(at=self.at, waits=waits))
        if not self.agenda:
            self.observe(SafePoint(at=self.at, status=self.status))
        state = self.state0.model_copy(
            update=dict(
                scenario=self.scenario,
                status=self.status,
                data=self.data,
                last_at=self.at,
                frames=self.frames,
                inflight=self.inflight,
                visits=self.visits,
                agenda=tuple(self.agenda),
                paused_queue=tuple(self.paused_queue),
                recent_stimuli=tuple(self.recent_stimuli),
                recent_controls=tuple(self.recent_controls),
                yield_seq=self.yield_seq,
                output=self.output,
                problem=self.problem,
                failed_node=self.failed_node,
                last_error=self.last_error,
            )
        )
        return Transition(state=state, commands=tuple(self.commands), observations=tuple(self.observations))
