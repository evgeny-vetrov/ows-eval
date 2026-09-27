import json
from datetime import timedelta

import pytest

from cases_errors import COMMUNICATION, ERROR_CASES

from ai.gena.services.fabula.engine.core.backoff import jitter_fraction, retry_delay
from ai.gena.services.fabula.engine.core.interpreter import CoreError, Interpreter
from ai.gena.services.fabula.engine.model.context import ExecutionContext
from ai.gena.services.fabula.engine.model.stimuli import InvocationFinished, OutcomeOk
from ai.gena.services.fabula.engine.ports.expressions import ExpressionResourceExhausted
from ai.gena.services.fabula.engine.testing.expressions import TestOnlyExpressionEngine
from ai.gena.services.fabula.engine.testing.kit import Kit, scenario_text
from ai.gena.services.fabula.engine.testing.runner import T0, Case, Finish, check_case, run_case


@pytest.mark.parametrize("case", ERROR_CASES, ids=str)
def test_errors(case: Case):
    check_case(case)


def test_jitter_is_deterministic_per_fabula_node_and_attempt():
    scenario = """
do:
  - guard:
      try:
        - ask: {call: 'echo:1@test'}
      catch:
        retry: {delay: PT10S, jitter: {from: PT1S, to: PT5S}, limit: {attempt: {count: 3}}}
"""
    log = check_case(Case("jitter", scenario, script=[Finish(node="/guard/try/ask", error=COMMUNICATION)]))
    [timer] = [c for c in log.commands if c.kind == "start_timer"]
    expected = timedelta(seconds=10) + timedelta(seconds=1) + timedelta(seconds=4 * jitter_fraction("f-1", "/guard", 1))
    assert abs((timer.fire_at - T0) - expected) < timedelta(milliseconds=1)
    fractions = {jitter_fraction(fabula, "/guard", attempt) for fabula in ("f-1", "f-2") for attempt in (1, 2)}
    assert len(fractions) == 4
    assert all(0 <= f < 1 for f in fractions)


@pytest.mark.parametrize(
    "backoff, retry, expected",
    [
        ("constant", 4, 10),
        ("linear", 4, 40),
        ("exponential", 4, 80),
        ("exponential", 200, 180 * 86400),
        ("exponential", 10**9, 180 * 86400),
        ("linear", 10**12, 180 * 86400),
    ],
)
def test_backoff_formulas(backoff, retry, expected):
    delay = retry_delay(
        base=timedelta(seconds=10),
        backoff=backoff,
        retry=retry,
        jitter_from=timedelta(0),
        jitter_to=timedelta(0),
        fabula_id="f",
        path="/p",
        cap=timedelta(days=180),
    )
    assert delay == timedelta(seconds=expected)


class ExplodingEngine(TestOnlyExpressionEngine):
    def __init__(self, error: Exception):
        super().__init__()
        self.error = error
        self.armed = False

    def evaluate(self, compiled, input, args):
        if self.armed:
            raise self.error
        return super().evaluate(compiled, input, args)


@pytest.mark.parametrize("error", [RuntimeError("bug in a port"), ExpressionResourceExhausted("wall clock budget")])
def test_core_bug_surfaces_as_core_error_and_keeps_the_state(error):
    kit = Kit()
    engine = ExplodingEngine(error)
    interpreter = Interpreter(expressions=engine, matcher=kit.matcher, schemas=kit.schemas)
    snapshot = kit.snapshot(scenario_text("do:\n  - ask: {call: 'echo:1@test'}\n  - next: {set: {v: '${ .v }'}}\n"))
    transition = interpreter.start(snapshot, None, ExecutionContext(), T0, fabula_id="f-1")
    before = transition.state.model_dump_json()
    engine.armed = True
    stimulus = InvocationFinished(stimulus_id="s1", at=T0, invocation_id="f-1/ask@1.1/call", outcome=OutcomeOk(output={"v": 1}))
    with pytest.raises(CoreError):
        interpreter.apply(transition.state, stimulus)
    assert transition.state.model_dump_json() == before
    engine.armed = False
    retried = interpreter.apply(transition.state, stimulus)
    assert retried.state.status == "completed"
    assert retried.state.output == {"v": 1}


def test_failed_problem_is_rfc7807():
    log = run_case(next(c for c in ERROR_CASES if c.name == "catch_mismatch_propagates"))
    [failed] = [c for c in log.commands if c.kind == "failed"]
    assert json.loads(failed.problem.model_dump_json(exclude_none=True)) == {
        "type": "https://serverlessworkflow.io/spec/1.0.0/errors/validation",
        "status": 400,
        "title": "Bad ticket",
        "instance": "/do/0/guard/try/0/check",
    }
