"""Scenario runner for tests.

A scenario test is a YAML scenario, a script of stimuli and the expected commands,
observations and final state. Script steps refer to operations by node path, so they
do not repeat the id scheme; the runner resolves them against the live state.

`check_case` runs every case twice — straight, and with a JSON round trip of the state
and a fresh interpreter after every transition — and requires byte-identical results.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ai.gena.services.fabula.engine.core.interpreter import Interpreter
from ai.gena.services.fabula.engine.core.limits import EngineLimits
from ai.gena.services.fabula.engine.core.run import Transition
from ai.gena.services.fabula.engine.core.state import FabulaState
from ai.gena.services.fabula.engine.model.context import ExecutionContext
from ai.gena.services.fabula.engine.model.durations import format_duration
from ai.gena.services.fabula.engine.model.event import Event
from ai.gena.services.fabula.engine.model.problem import OWS_ERRORS, Problem
from ai.gena.services.fabula.engine.model.scenario import ScenarioSnapshot
from ai.gena.services.fabula.engine.model.stimuli import (
    Control,
    EventDelivered,
    InvocationFinished,
    OutcomeCancelled,
    OutcomeError,
    OutcomeOk,
    TimerFired,
)
from ai.gena.services.fabula.engine.testing.kit import Kit, scenario_text

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
FABULA_ID = "f-1"
_UNSET = object()


@dataclass
class Step:
    at: datetime | None = None
    after: timedelta | None = None
    stimulus_id: str | None = None


@dataclass
class Finish(Step):
    """Complete the invocation of `node` (or `op_id`) with an output, an error or a cancel."""

    node: str | None = None
    output: Any = None
    error: Problem | None = None
    cancelled: bool = False
    op_id: str | None = None


@dataclass
class Fire(Step):
    """Fire the timer of `node` with the given role (`wait`, `timeout`, `retry`, ...)."""

    node: str | None = None
    role: str | None = None
    op_id: str | None = None


@dataclass
class Deliver(Step):
    node: str | None = None
    event: Event | None = None
    index: int = 0
    role: str = "sub"
    key: str | None = None
    op_id: str | None = None


@dataclass
class Act(Step):
    action: Any = None
    request_id: str | None = None
    actor: str = "operator"
    reason: str = "test"


@dataclass
class Raw(Step):
    build: Callable[[datetime, str], Any] | None = None


@dataclass
class TransitionLog:
    step: str
    commands: tuple
    observations: tuple
    state_json: str
    stimulus: Any = None


@dataclass
class RunLog:
    transitions: list[TransitionLog] = field(default_factory=list)
    state: FabulaState | None = None

    @property
    def commands(self) -> list:
        return [c for t in self.transitions for c in t.commands]

    @property
    def observations(self) -> list:
        return [o for t in self.transitions for o in t.observations]

    def fingerprint(self) -> list[str]:
        lines = []
        for t in self.transitions:
            lines.append(t.step)
            lines.extend(c.model_dump_json() for c in t.commands)
            lines.extend(o.model_dump_json() for o in t.observations)
            lines.append(t.state_json)
        return lines


def event(event_type: str, data: Any = None, *, id: str = "e-1", source: str = "test", subject: str | None = None, **attributes) -> Event:
    return Event(id=id, type=event_type, source=source, subject=subject, time=T0, data=data, attributes=attributes)


class ScenarioRunner:
    def __init__(self, kit: Kit, limits: EngineLimits = EngineLimits(), semantics: dict | None = None):
        self.kit = kit
        self.limits = limits
        self.semantics = semantics
        self.applied = 0

    def interpreter(self) -> Interpreter:
        return Interpreter(
            expressions=self.kit.expressions,
            matcher=self.kit.matcher,
            schemas=self.kit.schemas,
            limits=self.limits,
            new_fabula_semantics=self.semantics,
        )

    def run(
        self,
        snapshot: ScenarioSnapshot,
        script: list[Step],
        *,
        input: Any = None,
        roundtrip: bool | Callable[[str], FabulaState] = False,
        context: ExecutionContext = ExecutionContext(run_as="tester"),
        state: FabulaState | None = None,
        start_at: datetime = T0,
    ) -> RunLog:
        log = RunLog()
        interpreter = self.interpreter()
        if state is None:
            transition = interpreter.start(snapshot, input, context, start_at, fabula_id=FABULA_ID)
            state = self._record(log, "start", transition, roundtrip, None)
        now = state.last_at
        for step in script:
            if roundtrip:
                interpreter = self.interpreter()
            self.applied += 1
            number = self.applied
            stimulus_id = step.stimulus_id or f"s{number}"
            stimulus, now = self.stimulus(state, step, now, stimulus_id)
            transition = interpreter.apply(state, stimulus)
            state = self._record(log, f"step {number}: {type(step).__name__}", transition, roundtrip, stimulus)
        log.state = state
        return log

    @staticmethod
    def _record(log: RunLog, label: str, transition: Transition, roundtrip, stimulus) -> FabulaState:
        state_json = transition.state.model_dump_json()
        log.transitions.append(TransitionLog(label, transition.commands, transition.observations, state_json, stimulus))
        if callable(roundtrip):
            return roundtrip(state_json)
        return FabulaState.model_validate_json(state_json) if roundtrip else transition.state

    def stimulus(self, state: FabulaState, step: Step, now: datetime, stimulus_id: str):
        if isinstance(step, Finish):
            op_id = step.op_id or _find_op(state, step.node, "invocation", None).op_id
            at = _time(step, now)
            if step.cancelled:
                outcome = OutcomeCancelled()
            elif step.error is not None:
                outcome = OutcomeError(problem=step.error)
            else:
                outcome = OutcomeOk(output=step.output)
            return InvocationFinished(stimulus_id=stimulus_id, at=at, invocation_id=op_id, outcome=outcome), at
        if isinstance(step, Fire):
            if step.op_id:
                op_id, fire_at = step.op_id, None
            else:
                op = _find_op(state, step.node, "timer", step.role)
                op_id, fire_at = op.op_id, op.fire_at
            at = _time(step, max(now, fire_at) if fire_at and step.at is None and step.after is None else now)
            return TimerFired(stimulus_id=stimulus_id, at=at, timer_id=op_id), at
        if isinstance(step, Deliver):
            if step.op_id:
                op_id = step.op_id
            else:
                candidates = [op for op in state.inflight.values() if op.owner == step.node and op.op == "subscription" and op.role == step.role and op.index == step.index]
                if not candidates:
                    raise AssertionError(f"no {step.role}.{step.index} subscription for {step.node}: {list(state.inflight)}")
                op_id = candidates[0].op_id
            at = _time(step, now)
            key = step.key or f"{op_id}:{step.event.id}"
            return EventDelivered(stimulus_id=stimulus_id, at=at, subscription_id=op_id, delivery_key=key, event=step.event), at
        if isinstance(step, Act):
            at = _time(step, now)
            return Control(stimulus_id=stimulus_id, at=at, request_id=step.request_id or stimulus_id, actor=step.actor, reason=step.reason, action=step.action), at
        if isinstance(step, Raw):
            at = _time(step, now)
            return step.build(at, stimulus_id), at
        raise TypeError(f"unknown step {step!r}")


def _time(step: Step, now: datetime) -> datetime:
    if step.at is not None:
        return step.at
    if step.after is not None:
        return now + step.after
    return now


def _find_op(state: FabulaState, node: str, op: str, role: str | None):
    candidates = [o for o in state.inflight.values() if o.owner == node and o.op == op and (role is None or o.role == role)]
    if len(candidates) != 1:
        raise AssertionError(f"expected one {op}/{role} for {node}, found {[c.op_id for c in candidates]} in {list(state.inflight)}")
    return candidates[0]


def open_operations(log: RunLog) -> set[str]:
    """Operations started by commands and neither closed by a command nor consumed by a stimulus."""
    opened: set[str] = set()
    for transition in log.transitions:
        stimulus = transition.stimulus
        if isinstance(stimulus, TimerFired):
            opened.discard(stimulus.timer_id)
        elif isinstance(stimulus, InvocationFinished):
            opened.discard(stimulus.invocation_id)
        for command in transition.commands:
            if command.kind == "invoke_capability":
                opened.add(command.invocation_id)
            elif command.kind == "start_timer":
                opened.add(command.timer_id)
            elif command.kind == "subscribe":
                opened.add(command.subscription_id)
            elif command.kind == "cancel_invocation":
                opened.discard(command.invocation_id)
            elif command.kind == "cancel_timer":
                opened.discard(command.timer_id)
            elif command.kind == "unsubscribe":
                opened.discard(command.subscription_id)
    return opened


# --- summaries ------------------------------------------------------------------------------


def short_id(op_id: str) -> str:
    return op_id.split("/", 1)[1] if "/" in op_id else op_id


def offset(value: datetime | None, origin: datetime = T0) -> str:
    if value is None:
        return "never"
    delta = value - origin
    return ("-" if delta < timedelta(0) else "+") + format_duration(abs(delta))


def short_type(error_type: str) -> str:
    return error_type[len(OWS_ERRORS) :] if error_type.startswith(OWS_ERRORS) else error_type


def summarize_command(command) -> str:
    kind = command.kind
    if kind == "invoke_capability":
        return f"invoke {short_id(command.invocation_id)} {command.capability_ref}"
    if kind == "cancel_invocation":
        return f"cancel-invocation {short_id(command.invocation_id)}"
    if kind == "start_timer":
        return f"timer {short_id(command.timer_id)} {offset(command.fire_at)}"
    if kind == "cancel_timer":
        return f"cancel-timer {short_id(command.timer_id)}"
    if kind == "subscribe":
        return f"subscribe {short_id(command.subscription_id)} {','.join(command.event_types)}"
    if kind == "unsubscribe":
        return f"unsubscribe {short_id(command.subscription_id)}"
    if kind == "publish_event":
        return f"publish {short_id(command.emit_id)} {command.event.type}"
    if kind == "failed":
        return f"failed {short_type(command.problem.type)}"
    return kind


def summarize_observation(observation) -> str | None:
    kind = observation.kind
    if kind == "node_started":
        return f"start {observation.node} #{observation.visit}.{observation.attempt}"
    if kind == "node_completed":
        return f"done {observation.node}"
    if kind == "node_failed":
        return f"fail {observation.node} {short_type(observation.problem.type)}"
    if kind == "node_skipped":
        return f"skip {observation.node} ({observation.reason})"
    if kind == "node_cancelled":
        return f"cancel {observation.node}"
    if kind == "retry_scheduled":
        return f"retry {observation.node} -> {observation.next_attempt} at {offset(observation.fire_at)}"
    if kind == "stimulus_ignored":
        return f"ignored {observation.stimulus} ({observation.reason})"
    if kind == "stimulus_queued":
        return f"queued {observation.stimulus}"
    if kind == "control_accepted":
        return f"accepted {observation.action}"
    if kind == "control_rejected":
        return f"rejected {observation.action} ({observation.code})"
    if kind == "scenario_swapped":
        return f"swapped {observation.previous_ref} -> {observation.ref}"
    if kind in ("fabula_completed", "fabula_failed", "fabula_cancelled"):
        return kind.replace("fabula_", "fabula ")
    return None


# --- cases ---------------------------------------------------------------------------------------


@dataclass
class Case:
    name: str
    scenario: str
    script: list[Step] = field(default_factory=list)
    input: Any = None
    commands: list[str] | None = None
    observations: list[str] | None = None
    status: str | None = None
    output: Any = _UNSET
    data: Any = _UNSET
    limits: EngineLimits = EngineLimits()
    semantics: dict | None = None

    def __str__(self) -> str:
        return self.name


def case_text(case: Case) -> str:
    return case.scenario if case.scenario.lstrip().startswith("document:") else scenario_text(case.scenario, name=case.name.replace("_", "-").lower()[:40].strip("-") or "case")


def run_case(case: Case, kit: Kit | None = None, roundtrip: bool | Callable[[str], FabulaState] = False) -> RunLog:
    kit = kit or Kit()
    runner = ScenarioRunner(kit, limits=case.limits, semantics=case.semantics)
    return runner.run(kit.snapshot(case_text(case)), case.script, input=case.input, roundtrip=roundtrip)


def check_case(case: Case, kit: Kit | None = None) -> RunLog:
    """Run a case straight and with a round trip per transition, then check expectations."""
    straight = run_case(case, kit)
    replayed = run_case(case, kit, roundtrip=True)
    assert straight.fingerprint() == replayed.fingerprint(), f"{case.name}: replay with state reload diverged"
    # FR-11: whatever was opened is either still owned by the state or was closed.
    assert open_operations(straight) == set(straight.state.inflight), f"{case.name}: leaked {open_operations(straight) - set(straight.state.inflight)}"
    if case.commands is not None:
        assert [summarize_command(c) for c in straight.commands] == case.commands
    if case.observations is not None:
        assert [s for s in (summarize_observation(o) for o in straight.observations) if s] == case.observations
    state = straight.state
    if case.status is not None:
        assert state.status == case.status, f"status {state.status}, problem {state.problem}"
    if case.output is not _UNSET:
        assert state.output == case.output
    if case.data is not _UNSET:
        assert state.data == case.data
    return straight
