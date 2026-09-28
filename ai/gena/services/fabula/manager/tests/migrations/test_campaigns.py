import asyncio

import pytest

from ai.gena.services.fabula.engine.model.stimuli import OutcomeOk, Pause
from ai.gena.services.fabula.manager.model.campaigns import (
    ApplyCampaign,
    CampaignCommand,
    CampaignSource,
    CreateCampaign,
    Decide,
    DecisionSpec,
    ItemTarget,
)
from ai.gena.services.fabula.manager.model.errors import Conflict, InvalidInput
from ai.gena.services.fabula.manager.model.fabulas import ControlFabula
from ai.gena.services.fabula.manager.model.resolution import ResolutionPolicy
from ai.gena.services.fabula.manager.testing.faults import CrashingRuntime, ManagerCrash
from ai.gena.services.fabula.manager.testing.scenarios import (
    SCENARIO,
    V1,
    V2,
    V2_RENAMED,
    V3,
    V4,
    deliver_merged,
    finish_call,
    fleet,
    pending_invocations,
    publish,
)
from ai.gena.services.fabula.manager.testing.stand import ALICE, Stand

MIGRATION = '${ . + {owner: "ops"} }'
RESTART_BOTH = {"/ask": "/ask", "/merged": "/merged"}


def run(coroutine):
    return asyncio.run(coroutine)


class Fleet:
    """V1 fabulas at the call, at the event, failed and completed, plus a target."""

    def __init__(self, body: str, version: str):
        self.stand = Stand()
        self.stand.world.invoker.behaviours["strict:1@test"] = lambda request: OutcomeOk(output={"y": 1})
        run(publish(self.stand, V1, "1.0.0"))
        self.ids = run(fleet(self.stand))
        self.target = run(publish(self.stand, body, version))
        self.migrations = self.stand.manager.migrations

    def create(self, request_id: str = "c1", **fields):
        return run(self.migrations.create(ALICE, CreateCampaign(request_id=request_id, scenario=SCENARIO, target_version=self.target.version, **fields)))

    def decide(self, campaign_id: str, *specs: DecisionSpec, request_id: str = "d1"):
        return run(self.migrations.decide(ALICE, campaign_id, Decide(request_id=request_id, decisions=specs)))

    def apply(self, campaign_id: str, request_id: str = "a1", **fields):
        return run(self.migrations.apply(ALICE, campaign_id, ApplyCampaign(request_id=request_id, **fields)))

    def items(self, campaign_id: str):
        return {i.fabula_id: i for i in run(self.migrations.items(campaign_id)).items}

    def ref(self, role: str) -> str:
        return run(self.stand.runtime.state(self.ids[role])).scenario.ref

    def observations(self, role: str, kind: str):
        return [o for o in self.stand.journal.observations(self.ids[role]) if o.kind == kind]


def test_a_compatible_version_is_offered_in_flight_and_moves_only_after_approval():
    fleet_ = Fleet(V2, "2.0.0")
    campaign = fleet_.create()
    items = fleet_.items(campaign.campaign_id)
    assert campaign.status == "awaiting_decision"
    assert {fid: (i.verdict, i.state) for fid, i in items.items()} == {
        fleet_.ids["at_call"]: ("in_flight", "pending"),
        fleet_.ids["at_event"]: ("in_flight", "pending"),
        fleet_.ids["failed"]: ("in_flight", "pending"),
    }, "the completed fabula is not a candidate"
    assert {fleet_.ref(role) for role in fleet_.ids} == {"test/order:1.0.0"}
    decided = fleet_.decide(campaign.campaign_id, DecisionSpec(target=ItemTarget(verdicts=("in_flight",)), action="migrate"))
    assert decided.affected == {"approved": 3}
    assert run(fleet_.migrations.get(campaign.campaign_id)).status == "ready"
    result = fleet_.apply(campaign.campaign_id)
    assert result.processed == {"migrated": 3} and result.campaign.status == "done"
    assert [fleet_.ref(role) for role in ("at_call", "at_event", "failed", "done")] == ["test/order:2.0.0"] * 3 + ["test/order:1.0.0"]


def test_in_flight_operations_survive_and_the_fence_is_lifted():
    fleet_ = Fleet(V2, "2.0.0")
    campaign = fleet_.create()
    [invocation] = pending_invocations(fleet_.stand, fleet_.ids["at_call"])
    fleet_.decide(campaign.campaign_id, DecisionSpec(target=ItemTarget(fabula_ids=(fleet_.ids["at_call"],)), action="migrate"))
    fleet_.apply(campaign.campaign_id)
    state = run(fleet_.stand.runtime.state(fleet_.ids["at_call"]))
    assert state.status == "waiting" and invocation in state.inflight
    assert [o.request_id.rsplit(":", 1)[-1] for o in fleet_.observations("at_call", "control_accepted")] == ["pause", "swap", "resume"]
    run(finish_call(fleet_.stand, fleet_.ids["at_call"]))
    run(deliver_merged(fleet_.stand))
    state = run(fleet_.stand.runtime.state(fleet_.ids["at_call"]))
    assert (state.status, state.output) == ("completed", {"version": 2, "ticket": "T-at_call"})


def test_incompatible_changes_are_clustered_and_resolved_by_mapping_with_restarts():
    fleet_ = Fleet(V3, "3.0.0")
    campaign = fleet_.create()
    clusters = run(fleet_.migrations.clusters(campaign.campaign_id)).items
    assert sorted((c.verdict, c.count, c.problems[0].split(":")[0]) for c in clusters) == [
        ("blocked", 1, "'/merged'"),
        ("blocked", 2, "'/ask'"),
    ]
    with pytest.raises(InvalidInput):
        fleet_.decide(campaign.campaign_id, DecisionSpec(target=ItemTarget(verdicts=("blocked",)), action="resolve"))
    fleet_.decide(
        campaign.campaign_id,
        DecisionSpec(target=ItemTarget(verdicts=("blocked",)), action="resolve", mapping=RESTART_BOTH, context_migration=MIGRATION, approve=True),
    )
    items = fleet_.items(campaign.campaign_id)
    assert {i.verdict for i in items.values()} == {"with_restarts"}
    assert all(i.report.missing_variables == () for i in items.values())
    [echo] = pending_invocations(fleet_.stand, fleet_.ids["at_call"])
    result = fleet_.apply(campaign.campaign_id)
    assert result.processed == {"migrated": 3}
    assert echo in fleet_.stand.world.invoker.cancels, "the restarted call cancels the old invocation"
    for role in ("at_call", "failed"):
        state = run(fleet_.stand.runtime.state(fleet_.ids[role]))
        assert (state.scenario.ref, state.status, state.data["owner"]) == ("test/order:3.0.0", "waiting", "ops"), role
    assert [w.node for w in run(fleet_.stand.index.get(fleet_.ids["failed"])).waits] == ["/merged"], "the failed fabula came back"


def test_suggested_mapping_turns_a_rename_into_an_in_flight_move_and_revert_moves_back():
    fleet_ = Fleet(V2_RENAMED, "2.1.0")
    campaign = fleet_.create()
    assert campaign.suggested_mappings == {"test/order:1.0.0": {"/merged": "/prMerged"}}
    item = fleet_.items(campaign.campaign_id)[fleet_.ids["at_event"]]
    assert (item.verdict, [(m.old_node, m.new_node) for m in item.report.cursor]) == ("in_flight", [("/merged", "/prMerged")])
    fleet_.decide(campaign.campaign_id, DecisionSpec(target=ItemTarget(verdicts=("in_flight",)), action="migrate"))
    fleet_.apply(campaign.campaign_id)
    assert fleet_.ref("at_event") == "test/order:2.1.0"
    reverted = run(fleet_.migrations.revert(ALICE, campaign.campaign_id, CampaignCommand(request_id="undo")))
    [back_id] = reverted.campaign_ids
    back = run(fleet_.migrations.get(back_id))
    assert (back.origin, back.reverts, back.target_version, back.mapping) == ("revert", campaign.campaign_id, "1.0.0", {"/prMerged": "/merged"})
    fleet_.decide(back_id, DecisionSpec(target=ItemTarget(verdicts=("in_flight",)), action="migrate"))
    fleet_.apply(back_id)
    assert {fleet_.ref(role) for role in ("at_call", "at_event", "failed")} == {"test/order:1.0.0"}


def test_staying_keeps_the_version_and_frees_the_fabula_for_another_campaign():
    fleet_ = Fleet(V2, "2.0.0")
    first = fleet_.create("c1")
    second = fleet_.create("c2")
    claimed = fleet_.items(second.campaign_id)
    assert {i.state for i in claimed.values()} == {"not_eligible"}
    assert all(first.campaign_id in i.note for i in claimed.values())
    fleet_.decide(first.campaign_id, DecisionSpec(target=ItemTarget(verdicts=("in_flight",)), action="stay", reason="customer freeze"))
    assert run(fleet_.migrations.get(first.campaign_id)).status == "done"
    assert fleet_.ref("at_call") == "test/order:1.0.0"
    third = fleet_.create("c3")
    assert {i.state for i in fleet_.items(third.campaign_id).values()} == {"pending"}


def test_a_fabula_that_moved_after_approval_returns_to_the_decision():
    fleet_ = Fleet(V3, "3.0.0")
    campaign = fleet_.create(mapping={"/ask": "/ask"}, context_migration=MIGRATION)
    at_call = fleet_.ids["at_call"]
    assert fleet_.items(campaign.campaign_id)[at_call].verdict == "with_restarts"
    fleet_.decide(campaign.campaign_id, DecisionSpec(target=ItemTarget(fabula_ids=(at_call,)), action="migrate"))
    run(finish_call(fleet_.stand, at_call))
    result = fleet_.apply(campaign.campaign_id)
    item = fleet_.items(campaign.campaign_id)[at_call]
    assert result.processed == {"pending": 1}
    assert (item.state, item.verdict, item.attempt, item.note) == ("pending", "blocked", 2, "the analysis changed since the approval")
    assert fleet_.ref("at_call") == "test/order:1.0.0"
    state = run(fleet_.stand.runtime.state(at_call))
    assert state.status == "waiting", "the fence was lifted"
    assert "campaign.awaiting_decision" in fleet_.stand.notifier.kinds()


def test_an_operator_pause_is_respected():
    fleet_ = Fleet(V2, "2.0.0")
    at_call = fleet_.ids["at_call"]
    run(fleet_.stand.manager.fabulas.control(ALICE, at_call, ControlFabula(request_id="hold", action=Pause())))
    campaign = fleet_.create()
    fleet_.decide(campaign.campaign_id, DecisionSpec(target=ItemTarget(fabula_ids=(at_call,)), action="migrate"))
    fleet_.apply(campaign.campaign_id)
    state = run(fleet_.stand.runtime.state(at_call))
    assert (state.scenario.ref, state.status) == ("test/order:2.0.0", "paused")
    assert [o.request_id.rsplit(":", 1)[-1] for o in fleet_.observations("at_call", "control_accepted")] == ["hold", "swap"]


def test_cancel_fabula_needs_an_accepted_cancel_and_ends_the_item():
    fleet_ = Fleet(V4, "4.0.0")
    campaign = fleet_.create()
    at_call = fleet_.ids["at_call"]
    assert fleet_.items(campaign.campaign_id)[at_call].verdict == "blocked"
    result = fleet_.decide(campaign.campaign_id, DecisionSpec(target=ItemTarget(fabula_ids=(at_call,)), action="cancel_fabula", reason="obsolete"))
    assert result.affected == {"cancelled": 1}
    assert run(fleet_.stand.runtime.state(at_call)).status == "cancelled"


def test_auto_apply_compatible_approves_in_flight_items():
    fleet_ = Fleet(V2, "2.0.0")
    campaign = fleet_.create(auto_apply_compatible=True)
    assert campaign.status == "ready"
    assert fleet_.apply(campaign.campaign_id).processed == {"migrated": 3}


def test_campaign_requests_are_idempotent():
    fleet_ = Fleet(V2, "2.0.0")
    campaign = fleet_.create("c1")
    assert fleet_.create("c1") == campaign
    with pytest.raises(Conflict):
        fleet_.create("c1", reason="something else")
    spec = DecisionSpec(target=ItemTarget(verdicts=("in_flight",)), action="migrate")
    assert not fleet_.decide(campaign.campaign_id, spec, request_id="d").replayed
    assert fleet_.decide(campaign.campaign_id, spec, request_id="d").replayed
    assert fleet_.apply(campaign.campaign_id, "a").processed == {"migrated": 3}
    with pytest.raises(Conflict):
        fleet_.apply(campaign.campaign_id, "a")


def test_manual_policy_refuses_delegation():
    fleet_ = Fleet(V3, "3.0.0")
    campaign = fleet_.create()
    with pytest.raises(Conflict) as error:
        fleet_.decide(campaign.campaign_id, DecisionSpec(target=ItemTarget(verdicts=("blocked",)), action="delegate_to_agent"))
    assert error.value.code == "policy_manual"
    delegable = fleet_.create("c2", policy=ResolutionPolicy(mode="agent_proposes"))
    assert delegable.policy.mode == "agent_proposes"


def test_cancelling_a_campaign_leaves_every_fabula_where_it_is():
    fleet_ = Fleet(V2, "2.0.0")
    campaign = fleet_.create()
    fleet_.decide(campaign.campaign_id, DecisionSpec(target=ItemTarget(verdicts=("in_flight",)), action="migrate"))
    cancelled = run(fleet_.migrations.cancel(ALICE, campaign.campaign_id, CampaignCommand(request_id="stop")))
    assert (cancelled.status, cancelled.counts["state.staying"]) == ("cancelled", 3)
    assert {fleet_.ref(role) for role in fleet_.ids} == {"test/order:1.0.0"}
    with pytest.raises(Conflict):
        fleet_.apply(campaign.campaign_id)


def test_paused_campaigns_do_not_apply():
    fleet_ = Fleet(V2, "2.0.0")
    campaign = fleet_.create(auto_apply_compatible=True)
    run(fleet_.migrations.pause(ALICE, campaign.campaign_id, CampaignCommand(request_id="p")))
    with pytest.raises(Conflict):
        fleet_.apply(campaign.campaign_id)
    resumed = run(fleet_.migrations.resume(ALICE, campaign.campaign_id, CampaignCommand(request_id="r")))
    assert resumed.status == "ready"


def test_refresh_picks_up_new_candidates_and_new_positions():
    fleet_ = Fleet(V3, "3.0.0")
    campaign = fleet_.create()
    before = fleet_.items(campaign.campaign_id)[fleet_.ids["at_call"]]
    run(finish_call(fleet_.stand, fleet_.ids["at_call"]))
    run(fleet_.migrations.refresh(ALICE, campaign.campaign_id, CampaignCommand(request_id="r")))
    after = fleet_.items(campaign.campaign_id)[fleet_.ids["at_call"]]
    assert before.cluster_id != after.cluster_id
    assert after.report.cursor[0].old_node == "/merged"


def _baseline_calls() -> int:
    fleet_ = Fleet(V2, "2.0.0")
    fleet_.stand.deps.runtime = counter = CrashingRuntime(fleet_.stand.runtime, crash_at=0, after=False)
    campaign = fleet_.create(auto_apply_compatible=True)
    fleet_.apply(campaign.campaign_id)
    return counter.calls


MUTATING_CALLS = _baseline_calls()


@pytest.mark.parametrize("after", [False, True], ids=["before", "after"])
@pytest.mark.parametrize("crash_at", range(1, MUTATING_CALLS + 1))
def test_apply_survives_a_manager_crash_at_every_control(crash_at, after):
    fleet_ = Fleet(V2, "2.0.0")
    fleet_.stand.deps.runtime = CrashingRuntime(fleet_.stand.runtime, crash_at=crash_at, after=after)
    campaign = fleet_.create(auto_apply_compatible=True)
    for attempt in range(5):
        try:
            result = fleet_.apply(campaign.campaign_id, f"a{attempt}")
            break
        except ManagerCrash:
            continue
    assert result.campaign.status == "done" and result.campaign.counts["state.migrated"] == 3
    for role in ("at_call", "at_event", "failed"):
        state = run(fleet_.stand.runtime.state(fleet_.ids[role]))
        assert state.scenario.ref == "test/order:2.0.0" and state.status != "paused", role
        assert len(fleet_.observations(role, "scenario_swapped")) == 1, role


# --- regressions -------------------------------------------------------------------


def _crash_apply(fleet_: Fleet, campaign_id: str, crash_at: int) -> None:
    fleet_.stand.deps.runtime = CrashingRuntime(fleet_.stand.runtime, crash_at=crash_at, after=True)
    with pytest.raises(ManagerCrash):
        fleet_.apply(campaign_id)
    fleet_.stand.deps.runtime = fleet_.stand.runtime


def test_cancel_after_a_crash_behind_the_fence_resumes_the_fabula():
    fleet_ = Fleet(V2, "2.0.0")
    campaign = fleet_.create(source=CampaignSource(fabula_ids=(fleet_.ids["at_call"],)), auto_apply_compatible=True)
    _crash_apply(fleet_, campaign.campaign_id, crash_at=1)
    assert fleet_.items(campaign.campaign_id)[fleet_.ids["at_call"]].fence == "pausing"
    run(fleet_.migrations.cancel(ALICE, campaign.campaign_id, CampaignCommand(request_id="stop")))
    state = run(fleet_.stand.runtime.state(fleet_.ids["at_call"]))
    assert (state.status, state.scenario.ref) == ("waiting", "test/order:1.0.0")


def test_cancel_after_a_crash_behind_the_swap_records_the_migration():
    fleet_ = Fleet(V2, "2.0.0")
    campaign = fleet_.create(source=CampaignSource(fabula_ids=(fleet_.ids["at_call"],)), auto_apply_compatible=True)
    _crash_apply(fleet_, campaign.campaign_id, crash_at=2)
    run(fleet_.migrations.cancel(ALICE, campaign.campaign_id, CampaignCommand(request_id="stop")))
    item = fleet_.items(campaign.campaign_id)[fleet_.ids["at_call"]]
    state = run(fleet_.stand.runtime.state(fleet_.ids["at_call"]))
    assert (item.state, state.status, state.scenario.ref) == ("migrated", "waiting", "test/order:2.0.0")
    assert run(fleet_.migrations.revert(ALICE, campaign.campaign_id, CampaignCommand(request_id="undo"))).campaign_ids


def test_revert_restarts_what_the_forward_mapping_restarted_even_on_a_deprecated_version():
    from ai.gena.services.fabula.manager.model.scenarios import DeprecateVersion
    from ai.gena.services.fabula.manager.model.tags import MoveTag

    fleet_ = Fleet(V3, "3.0.0")
    campaign = fleet_.create(mapping=RESTART_BOTH, context_migration=MIGRATION)
    fleet_.decide(campaign.campaign_id, DecisionSpec(target=ItemTarget(verdicts=("with_restarts",)), action="migrate"))
    assert fleet_.apply(campaign.campaign_id).processed == {"migrated": 3}
    run(fleet_.stand.manager.tags.move(ALICE, SCENARIO, "trunk", MoveTag(request_id="t", version="3.0.0", propose_migration=False)))
    run(fleet_.stand.manager.registry.deprecate(ALICE, SCENARIO, "1.0.0", DeprecateVersion(request_id="dep")))
    [back_id] = run(fleet_.migrations.revert(ALICE, campaign.campaign_id, CampaignCommand(request_id="undo"))).campaign_ids
    back = run(fleet_.migrations.get(back_id))
    assert back.mapping == RESTART_BOTH
    assert {i.verdict for i in fleet_.items(back_id).values()} == {"with_restarts"}


def test_a_replayed_creation_finishes_an_interrupted_analysis():
    fleet_ = Fleet(V2, "2.0.0")
    store = fleet_.stand.deps.campaigns
    original = store.claim
    calls = []

    async def flaky(*args):
        calls.append(args)
        if len(calls) == 1:
            raise ConnectionError("store blip")
        return await original(*args)

    store.claim = flaky
    with pytest.raises(ConnectionError):
        fleet_.create()
    campaign = fleet_.create()
    assert campaign.counts == {"state.pending": 3, "verdict.in_flight": 3}
    assert campaign.analyzed_at is not None


def test_decided_or_redelegated_items_never_leave_tasks_behind():
    fleet_ = Fleet(V3, "3.0.0")
    campaign = fleet_.create(policy=ResolutionPolicy(mode="agent_proposes"))
    delegate = DecisionSpec(target=ItemTarget(verdicts=("blocked",)), action="delegate_to_agent")
    first = fleet_.decide(campaign.campaign_id, delegate)
    again = fleet_.decide(campaign.campaign_id, delegate, request_id="d2")
    assert len(first.task_ids) == 2 and again.task_ids == () and len(again.skipped) == 3
    resolve = DecisionSpec(target=ItemTarget(verdicts=("blocked",)), action="resolve", mapping=RESTART_BOTH, context_migration=MIGRATION, approve=True)
    fleet_.decide(campaign.campaign_id, resolve, request_id="d3")
    tasks = run(fleet_.stand.manager.resolution.list(campaign_id=campaign.campaign_id)).items
    assert {t.status for t in tasks} == {"closed"}
