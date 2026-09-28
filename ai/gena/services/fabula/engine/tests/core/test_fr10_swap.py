import pytest

from cases_swap import MERGED, SNAPSHOT_V1, SNAPSHOT_V2, SNAPSHOT_V3, SWAP_CASES, V1_TEXT

from ai.gena.services.fabula.engine.model.stimuli import Cancel, SwapVersion
from ai.gena.services.fabula.engine.testing.kit import Kit
from ai.gena.services.fabula.engine.testing.runner import Act, Case, Deliver, Finish, ScenarioRunner, check_case, run_case

KIT = Kit()


@pytest.mark.parametrize("case", SWAP_CASES, ids=str)
def test_swap(case: Case):
    check_case(case)


def _waiting_on_event():
    runner = ScenarioRunner(KIT)
    return runner.run(SNAPSHOT_V1, [Finish(node="/ask", output={})], input={"ticket": "T-1"}).state


def _waiting_on_call():
    return ScenarioRunner(KIT).run(SNAPSHOT_V1, [], input={"ticket": "T-1"}).state


def test_analyze_compatible_swap_keeps_operations():
    state = _waiting_on_call()
    report = ScenarioRunner(KIT).interpreter().analyze_swap(state, SNAPSHOT_V2, {})
    assert report.applicable and report.compatible
    assert [(m.old_node, m.new_node, m.disposition) for m in report.cursor] == [("/ask", "/ask", "kept")]
    assert [(o.op_id, o.disposition) for o in report.operations] == [("f-1/ask@1.1/call", "kept")]
    assert report.restarts == ()


def test_analyze_incompatible_swap_explains_the_rejection():
    state = _waiting_on_event()
    report = ScenarioRunner(KIT).interpreter().analyze_swap(state, SNAPSHOT_V3, {})
    assert not report.applicable
    assert [(m.old_node, m.disposition) for m in report.cursor] == [("/merged", "unmapped")]
    assert report.problems == ("'/merged': event filters or consumption strategy changed; map it explicitly to restart it",)
    assert report.missing_variables == ("$context.owner",)


def test_analyze_with_mapping_and_migration():
    state = _waiting_on_event()
    interpreter = ScenarioRunner(KIT).interpreter()
    report = interpreter.analyze_swap(state, SNAPSHOT_V3, {"/merged": "/merged"}, context_migration='${ . + {owner: "x"} }')
    assert report.applicable and not report.compatible
    assert report.restarts == ("/merged",)
    assert [(o.op_id, o.disposition) for o in report.operations] == [("f-1/merged@1.1/sub.0", "cancelled")]
    assert report.missing_variables == ()


def test_rejected_swap_carries_the_report_and_changes_nothing():
    log = run_case(Case("reject", V1_TEXT, input={"ticket": "T-1"}, script=[Act(action=SwapVersion(scenario=SNAPSHOT_V3))]))
    [rejected] = [o for o in log.observations if o.kind == "control_rejected"]
    assert rejected.report is not None and not rejected.report.applicable
    assert log.state.scenario.ref == "test/swap:1.0.0"
    assert log.transitions[-1].commands == ()


def test_preview_matches_the_applied_swap():
    state = _waiting_on_event()
    interpreter = ScenarioRunner(KIT).interpreter()
    migration = '${ . + {owner: "x"} }'
    preview = interpreter.analyze_swap(state, SNAPSHOT_V3, {"/merged": "/merged"}, migration)
    log = ScenarioRunner(KIT).run(
        SNAPSHOT_V1, [Act(action=SwapVersion(scenario=SNAPSHOT_V3, mapping={"/merged": "/merged"}, context_migration=migration), stimulus_id="swap")], state=state
    )
    [swapped] = [o for o in log.observations if o.kind == "scenario_swapped"]
    assert swapped.report == preview
    assert log.state.scenario.digest == SNAPSHOT_V3.digest


def test_swapped_fabula_survives_reload_and_semantics_stay_pinned():
    case = next(c for c in SWAP_CASES if c.name == "swap_incompatible_with_mapping_and_migration")
    log = check_case(case)
    assert log.state.semantics.engine_version == "0.1.0"
    assert log.state.scenario.ref == "test/swap:3.0.0"


@pytest.mark.parametrize("script, status", [([Finish(node="/ask", output={}), Deliver(node="/merged", event=MERGED)], "completed"), ([Act(action=Cancel())], "cancelled")])
def test_analysis_of_a_finished_fabula_reports_instead_of_failing(script, status):
    state = ScenarioRunner(KIT).run(SNAPSHOT_V1, script, input={"ticket": "T-1"}).state
    assert state.status == status
    report = ScenarioRunner(KIT).interpreter().analyze_swap(state, SNAPSHOT_V2, {})
    assert (report.applicable, report.problems) == (False, (f"not allowed while the fabula is {status}",))
