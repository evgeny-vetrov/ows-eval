import pytest

from cases_regressions import REGRESSION_CASES

from ai.gena.services.fabula.engine.testing.runner import Case, check_case


@pytest.mark.parametrize("case", REGRESSION_CASES, ids=str)
def test_regression(case: Case):
    check_case(case)
