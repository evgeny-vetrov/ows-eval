import time

import pytest

from cases_loops import LOOP_CASES

from ai.gena.services.fabula.engine.core.limits import EngineLimits
from ai.gena.services.fabula.engine.testing.kit import Kit, scenario_text
from ai.gena.services.fabula.engine.testing.runner import Case, Fire, ScenarioRunner, check_case

COUNTER = scenario_text(
    """\
    do:
      - loop:
          for: {in: '${ .items }'}
          do:
            - count:
                set: {}
                export: {as: '${ {count: (($context.count // 0) + 1)} }'}
      - total: {set: {count: '${ $context.count }'}}
    """
)


@pytest.mark.parametrize("case", LOOP_CASES, ids=str)
def test_loops(case: Case):
    check_case(case)


def test_ten_thousand_iterations_are_fast_and_keep_the_state_flat():
    kit = Kit()
    runner = ScenarioRunner(kit, limits=EngineLimits(max_steps_per_transition=1000))
    snapshot = kit.snapshot(COUNTER)
    items = list(range(10_000))
    started = time.perf_counter()
    log = runner.run(snapshot, [], input={"items": items})
    state = log.state
    yields = 0
    sizes = []
    while state.status == "running":
        [yield_op] = [op for op in state.inflight.values() if op.role == "yield"]
        log = runner.run(snapshot, [Fire(op_id=yield_op.op_id)], state=state)
        state = log.state
        sizes.append(len(log.transitions[-1].state_json))
        yields += 1
    elapsed = time.perf_counter() - started
    assert state.status == "completed"
    assert state.output == {"count": 10_000}
    assert yields >= 9
    # Between yields the loop index and the counter change, nothing accumulates.
    assert max(sizes[:-1]) - min(sizes[:-1]) < 64
    assert elapsed < 10, f"10k iterations took {elapsed:.1f}s"


def test_yield_points_do_not_change_the_result():
    kit = Kit()
    snapshot = kit.snapshot(COUNTER)
    items = list(range(300))
    results = []
    for budget in (50, 20_000):
        runner = ScenarioRunner(kit, limits=EngineLimits(max_steps_per_transition=budget))
        state = runner.run(snapshot, [], input={"items": items}).state
        while state.status == "running":
            [yield_op] = [op for op in state.inflight.values() if op.role == "yield"]
            state = runner.run(snapshot, [Fire(op_id=yield_op.op_id)], state=state).state
        results.append((state.status, state.output, state.data))
    assert results[0] == results[1] == ("completed", {"count": 300}, {"count": 300})
