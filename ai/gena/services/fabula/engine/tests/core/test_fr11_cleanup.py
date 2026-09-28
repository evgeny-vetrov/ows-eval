"""FR-11: leaving a scope in any way closes everything the scope held.

`check_case` asserts the no-leak invariant for every scenario case; this module names
the ways out of a scope and makes sure each of them is exercised with operations in
flight.
"""

import pytest

from all_cases import ALL_CASES

from ai.gena.services.fabula.engine.testing.runner import check_case, open_operations

EXITS = [
    ("cancel", "cancel_closes_everything", "fabula cancelled"),
    ("compete", "fork_compete_first_wins_and_losers_are_closed", "lost the race"),
    ("goto", "goto_out_of_fork_needs_permission", "goto"),
    ("goto out of a loop", "goto_out_of_loop_needs_permission", "goto"),
    ("end", "then_end_inside_a_branch_closes_other_branches", "workflow ended"),
    ("error in a sibling branch", "fork_branch_failure_cancels_siblings", "sibling branch failed"),
    ("parent deadline", "group_deadline_cancels_everything_inside", "failed"),
    ("workflow deadline", "workflow_deadline", "workflow failed"),
    ("version swap", "swap_incompatible_with_mapping_and_migration", "version swap"),
    ("retry of a node", "retry_task_reissues_the_current_call", "retried"),
]


@pytest.mark.parametrize("exit_kind, case_name, reason", EXITS, ids=[e[0] for e in EXITS])
def test_scope_exit_closes_operations(exit_kind, case_name, reason):
    case = next(c for c in ALL_CASES if c.name == case_name)
    log = check_case(case)
    cancelled = [o for o in log.observations if o.kind == "node_cancelled" and o.reason == reason]
    assert cancelled, f"{exit_kind}: no node was cancelled with reason '{reason}'"
    closed = {c.kind for c in log.commands} & {"cancel_invocation", "cancel_timer", "unsubscribe"}
    assert closed, f"{exit_kind}: nothing was closed"
    assert open_operations(log) == set(log.state.inflight)


def test_terminal_fabulas_hold_nothing():
    for case in ALL_CASES:
        log = check_case(case)
        if log.state.status in ("completed", "failed", "cancelled"):
            assert log.state.inflight == {} and open_operations(log) == set(), case.name
