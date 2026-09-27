PY3TEST()

SIZE(SMALL)

PY_SRCS(
    TOP_LEVEL
    all_cases.py
    cases_call.py
    cases_control.py
    cases_errors.py
    cases_events.py
    cases_flow.py
    cases_fork.py
    cases_loops.py
    cases_regressions.py
    cases_swap.py
    cases_timers.py
    state_v1_fixture.py
)

TEST_SRCS(
    test_fr02_flow.py
    test_fr03_call.py
    test_fr04_timers.py
    test_fr05_events.py
    test_fr06_errors.py
    test_fr07_loops.py
    test_fr08_fork.py
    test_fr09_long_life.py
    test_fr10_control.py
    test_fr10_swap.py
    test_fr11_cleanup.py
    test_fr12_observability.py
    test_performance.py
    test_regressions.py
    test_replay.py
    test_semantics_flags.py
    test_state_compat.py
)

PEERDIR(
    ai/gena/services/fabula/engine
)

END()
