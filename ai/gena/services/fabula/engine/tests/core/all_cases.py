"""Every scenario case of the core tests, for suites that run them all."""

from cases_call import CALL_CASES
from cases_control import CONTROL_CASES
from cases_errors import ERROR_CASES
from cases_events import EVENT_CASES
from cases_flow import FLOW_CASES
from cases_fork import FORK_CASES
from cases_loops import LOOP_CASES
from cases_regressions import REGRESSION_CASES
from cases_swap import SWAP_CASES
from cases_timers import TIMER_CASES

ALL_CASES = [
    *FLOW_CASES,
    *CALL_CASES,
    *TIMER_CASES,
    *EVENT_CASES,
    *ERROR_CASES,
    *LOOP_CASES,
    *FORK_CASES,
    *CONTROL_CASES,
    *SWAP_CASES,
    *REGRESSION_CASES,
]
