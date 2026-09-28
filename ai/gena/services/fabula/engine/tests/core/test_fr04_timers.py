import pytest

from cases_timers import TIMER_CASES

from ai.gena.services.fabula.engine.testing.runner import Case, check_case, run_case


@pytest.mark.parametrize("case", TIMER_CASES, ids=str)
def test_timers(case: Case):
    check_case(case)


def test_waiting_status_while_timer_pending():
    case = next(c for c in TIMER_CASES if c.name == "wait_duration")
    log = run_case(Case(case.name, case.scenario))
    assert log.state.status == "waiting"
    [waiting] = [o for o in log.observations if o.kind == "waiting"]
    assert [(w.node, w.wait, w.until) for w in waiting.waits] == [("/pause", "timer", log.commands[0].fire_at)]
