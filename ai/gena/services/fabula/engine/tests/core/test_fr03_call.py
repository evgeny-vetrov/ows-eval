from datetime import timedelta

import pytest

from cases_call import CALL_CASES

from ai.gena.services.fabula.engine.model.context import ExecutionContext
from ai.gena.services.fabula.engine.testing.kit import Kit, scenario_text
from ai.gena.services.fabula.engine.testing.runner import Case, Finish, ScenarioRunner, check_case


@pytest.mark.parametrize("case", CALL_CASES, ids=str)
def test_call(case: Case):
    check_case(case)


def test_invocation_carries_input_context_and_remaining_deadline():
    kit = Kit()
    snapshot = kit.snapshot(
        scenario_text(
            """\
            do:
              - ask:
                  call: 'slow:1@test'
                  with: {q: '${ .q }'}
                  timeout: {after: PT10M}
            """
        )
    )
    context = ExecutionContext(run_as="robot@gena", trigger={"ticket": "T-1"}, labels={"team": "gena"})
    log = ScenarioRunner(kit).run(snapshot, [], input={"q": 42}, context=context)
    [timer, invoke] = log.commands
    assert invoke.input == {"q": 42}
    assert invoke.context == context
    assert invoke.timeout == timedelta(minutes=10)
    assert invoke.invocation_id == "f-1/ask@1.1/call"
    assert timer.timer_id == "f-1/ask@1.1/timeout"


def test_result_equal_ids_across_runs():
    case = CALL_CASES[0]
    first = check_case(case)
    second = check_case(case)
    assert [c.model_dump_json() for c in first.commands] == [c.model_dump_json() for c in second.commands]


def test_unknown_invocation_is_ignored_without_side_effects():
    kit = Kit()
    snapshot = kit.snapshot(scenario_text("do:\n  - ask: {call: 'echo:1@test'}\n"))
    runner = ScenarioRunner(kit)
    log = runner.run(snapshot, [Finish(op_id="f-1/other@1.1/call", output=1)])
    before, after = log.transitions[0].state_json, log.transitions[1].state_json
    assert after.replace('"s1"', "") .count("inflight") == before.count("inflight")
    assert [o.kind for o in log.transitions[1].observations] == ["stimulus_ignored", "safe_point"]
    assert log.transitions[1].commands == ()
