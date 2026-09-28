import pytest

from cases_control import CONTROL_CASES, SEQUENCE

from ai.gena.services.fabula.engine.model.stimuli import Pause, SkipTask
from ai.gena.services.fabula.engine.testing.runner import Act, Case, Finish, check_case, run_case


@pytest.mark.parametrize("case", CONTROL_CASES, ids=str)
def test_control(case: Case):
    check_case(case)


def test_interventions_are_journaled_with_actor_and_reason():
    log = run_case(Case("journal", SEQUENCE, script=[Act(action=Pause(), actor="alice", reason="investigating", request_id="req-1")]))
    [accepted] = [o for o in log.observations if o.kind == "control_accepted"]
    assert (accepted.request_id, accepted.actor, accepted.reason, accepted.action) == ("req-1", "alice", "investigating", "pause")


def test_rejected_intervention_leaves_the_state_untouched():
    log = run_case(Case("reject", SEQUENCE, script=[Act(action=SkipTask(node="/rest"))]))
    before, after = log.transitions[0].state_json, log.transitions[1].state_json
    assert _without_bookkeeping(before) == _without_bookkeeping(after)
    assert log.transitions[1].commands == ()


def test_no_new_work_is_started_while_paused():
    case = next(c for c in CONTROL_CASES if c.name == "pause_accumulates_and_resume_applies_in_order")
    log = run_case(case)
    paused = log.transitions[1:3]
    assert [c for t in paused for c in t.commands] == []
    assert log.transitions[2].observations[0].kind == "stimulus_queued"


def _without_bookkeeping(state_json: str) -> str:
    import json

    state = json.loads(state_json)
    for key in ("recent_stimuli", "recent_controls", "last_at"):
        state.pop(key)
    return json.dumps(state, sort_keys=True)


def test_duplicate_stimulus_id_does_not_change_the_result():
    script = [Finish(node="/ask", output=1, stimulus_id="same"), Finish(op_id="f-1/ask@1.1/call", output=2, stimulus_id="same")]
    log = run_case(Case("dup", SEQUENCE, script=script))
    assert log.transitions[2].state_json == log.transitions[1].state_json
    assert [o.reason for o in log.transitions[2].observations] == ["duplicate"]
