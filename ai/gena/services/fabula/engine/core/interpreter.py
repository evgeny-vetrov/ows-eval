"""The pure transition function: `start` and `apply`."""

from collections import OrderedDict
from datetime import datetime
from typing import Any

from ai.gena.services.fabula.engine.core.control import Controls
from ai.gena.services.fabula.engine.core.limits import EngineLimits
from ai.gena.services.fabula.engine.core.run import ACTIVE, Transition
from ai.gena.services.fabula.engine.core.semantics import DEFAULT_FLAGS, Semantics, validate_flags
from ai.gena.services.fabula.engine.core.state import FabulaState
from ai.gena.services.fabula.engine.core.swap import Swapping
from ai.gena.services.fabula.engine.dsl.compiler import ProfileLimits
from ai.gena.services.fabula.engine.dsl.diagnostics import InvalidScenario
from ai.gena.services.fabula.engine.dsl.graph import Graph
from ai.gena.services.fabula.engine.dsl.loader import compile_snapshot
from ai.gena.services.fabula.engine.model.base import to_utc
from ai.gena.services.fabula.engine.model.context import ExecutionContext
from ai.gena.services.fabula.engine.model.observations import ControlRejected, FabulaStarted, StimulusQueued
from ai.gena.services.fabula.engine.model.observations import FINAL_STATUSES
from ai.gena.services.fabula.engine.model.scenario import ScenarioSnapshot
from ai.gena.services.fabula.engine.model.stimuli import Control, Stimulus
from ai.gena.services.fabula.engine.model.swap import SwapReport
from ai.gena.services.fabula.engine.ports.expressions import ExpressionEngine
from ai.gena.services.fabula.engine.ports.matching import EventMatcher
from ai.gena.services.fabula.engine.ports.schemas import SchemaValidator
from ai.gena.services.fabula.engine.version import ENGINE_VERSION, STATE_SCHEMA_VERSION

_GRAPH_CACHE_SIZE = 64


class CoreError(Exception):
    """A bug inside the core (or a non-deterministic port failure).

    The transition is aborted; the state passed in is untouched and may be retried.
    """


class InvalidState(Exception):
    pass


class Run(Controls, Swapping):
    def handle(self, stimulus: Stimulus) -> None:
        self.remember_stimulus(stimulus.stimulus_id)
        if self.status in FINAL_STATUSES:
            if isinstance(stimulus, Control):
                self.reject_final(stimulus)
            else:
                self.ignore(stimulus, "final", f"the fabula is {self.status}")
            return
        if isinstance(stimulus, Control):
            self.on_control(stimulus)
        elif self.status == "paused":
            self.enqueue(stimulus)
        else:
            self.activate()
            self.route(stimulus)
        self.activate()
        self.run_agenda()

    def route(self, stimulus: Stimulus) -> None:
        if stimulus.kind == "invocation_finished":
            op = self.inflight.get(stimulus.invocation_id)
            if op is None or op.op != "invocation":
                self.ignore(stimulus, "late", "invocation is not in flight")
                return
            del self.inflight[op.op_id]
            self.on_invocation_result(op.owner, stimulus.outcome)
        elif stimulus.kind == "timer_fired":
            op = self.inflight.get(stimulus.timer_id)
            if op is None or op.op != "timer":
                self.ignore(stimulus, "late", "timer is not active")
                return
            del self.inflight[op.op_id]
            self.on_timer(op)
        else:
            self.on_event(stimulus)

    def enqueue(self, stimulus: Stimulus) -> None:
        if len(self.paused_queue) >= self.limits.max_paused_stimuli:
            # Not applied, so not remembered: a redelivery with the same id must get through.
            self.recent_stimuli.remove(stimulus.stimulus_id)
            self.ignore(stimulus, "paused_backlog_full", f"more than {self.limits.max_paused_stimuli} stimuli arrived during the pause")
            return
        self.paused_queue.append(stimulus)
        self.observe(StimulusQueued(at=self.at, stimulus_id=stimulus.stimulus_id, stimulus=stimulus.kind))

    def reject_final(self, stimulus: Control) -> None:
        self.observe(
            ControlRejected(
                at=self.at,
                request_id=stimulus.request_id,
                actor=stimulus.actor,
                reason=stimulus.reason,
                action=stimulus.action.type,
                code="final",
                message=f"the fabula is {self.status}",
            )
        )


class Interpreter:
    """Pure, synchronous transition function over `FabulaState`.

    Pure ports are given at construction. The instance keeps nothing but a memo of
    compiled graphs keyed by scenario digest.
    """

    def __init__(
        self,
        *,
        expressions: ExpressionEngine,
        matcher: EventMatcher,
        schemas: SchemaValidator,
        limits: EngineLimits = EngineLimits(),
        profile_limits: ProfileLimits = ProfileLimits(),
        new_fabula_semantics: dict[str, str] | None = None,
    ):
        self.expressions = expressions
        self.matcher = matcher
        self.schemas = schemas
        self.limits = limits
        self.profile_limits = profile_limits
        self.new_fabula_flags = validate_flags(DEFAULT_FLAGS if new_fabula_semantics is None else {**DEFAULT_FLAGS, **new_fabula_semantics})
        self._graphs: OrderedDict[str, Graph] = OrderedDict()

    def graph(self, scenario: ScenarioSnapshot) -> Graph:
        graph = self._graphs.get(scenario.digest)
        if graph is None:
            graph = compile_snapshot(scenario, expressions=self.expressions, schemas=self.schemas, limits=self.profile_limits)
            self._graphs[scenario.digest] = graph
            if len(self._graphs) > _GRAPH_CACHE_SIZE:
                self._graphs.popitem(last=False)
        else:
            self._graphs.move_to_end(scenario.digest)
        return graph

    def start(
        self,
        scenario: ScenarioSnapshot,
        input: Any,
        context: ExecutionContext,
        at: datetime,
        *,
        fabula_id: str,
    ) -> Transition:
        graph = self.graph(scenario)
        at = to_utc(at)
        state = FabulaState(
            semantics=Semantics(engine_version=ENGINE_VERSION, flags=self.new_fabula_flags),
            fabula_id=fabula_id,
            status="running",
            scenario=scenario,
            execution_context=context,
            workflow_input=input,
            started_at=at,
            last_at=at,
        )
        try:
            run = Run(self, graph, state, at)
            run.observe(FabulaStarted.model_construct(at=at, scenario_ref=scenario.ref, input=input))
            run.begin(input)
            run.run_agenda()
            return run.finalize()
        except InvalidScenario:
            raise
        except Exception as exc:
            raise CoreError(f"core failed to start fabula {fabula_id}: {exc!r}") from exc

    def analyze_swap(
        self,
        state: FabulaState,
        new_scenario: ScenarioSnapshot,
        mapping: dict[str, str] | None = None,
        context_migration: str | None = None,
    ) -> SwapReport:
        """Pure preview of `SwapVersion`: where the cursor lands, which operations survive,
        which nodes restart and which context keys the new version lacks."""
        run = Run(self, self.graph(state.scenario), state, state.last_at)
        if run.agenda:
            return SwapReport(applicable=False, compatible=False, from_ref=state.scenario.ref, to_ref=new_scenario.ref, problems=("the fabula has pending work",))
        return run.plan_swap(new_scenario, mapping or {}, context_migration).report

    def apply(self, state: FabulaState, stimulus: Stimulus) -> Transition:
        if state.schema_version != STATE_SCHEMA_VERSION:
            raise InvalidState(f"state schema {state.schema_version} is not {STATE_SCHEMA_VERSION}; load it through core.codec")
        if stimulus.stimulus_id in state.recent_stimuli:
            run = Run(self, self.graph(state.scenario), state, stimulus.at)
            run.ignore(stimulus, "duplicate", "stimulus_id was already applied")
            return Transition(state=state, commands=(), observations=tuple(run.observations))
        try:
            run = Run(self, self.graph(state.scenario), state, to_utc(stimulus.at))
            run.handle(stimulus)
            return run.finalize()
        except InvalidScenario:
            raise
        except Exception as exc:
            raise CoreError(f"core failed on stimulus {stimulus.stimulus_id} of fabula {state.fabula_id}: {exc!r}") from exc


__all__ = ["ACTIVE", "CoreError", "Interpreter", "InvalidState", "Transition"]
