import pytest

from cases_fork import FORK_CASES

from ai.gena.services.fabula.engine.testing.runner import Case, check_case


@pytest.mark.parametrize("case", FORK_CASES, ids=str)
def test_fork(case: Case):
    check_case(case)
