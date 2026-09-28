import pytest

from cases_flow import FLOW_CASES

from ai.gena.services.fabula.engine.testing.runner import Case, check_case, run_case


@pytest.mark.parametrize("case", FLOW_CASES, ids=str)
def test_flow(case: Case):
    check_case(case)


def test_unhandled_error_is_recorded_not_raised():
    log = run_case(next(c for c in FLOW_CASES if c.name == "raise_unhandled_fails_the_fabula"))
    problem = log.state.problem
    assert (problem.status, problem.title, problem.detail, problem.instance) == (409, "Boom", "value 7", "/do/0/boom")
    assert log.state.failed_node == "/boom"
    assert log.state.inflight == {}
