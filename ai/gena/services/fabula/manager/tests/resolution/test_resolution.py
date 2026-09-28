import asyncio
from datetime import timedelta

import pytest

from ai.gena.services.fabula.engine.model.stimuli import OutcomeOk
from ai.gena.services.fabula.manager.agents.prompt import UnparsableAnswer, parse_proposal, render_brief
from ai.gena.services.fabula.manager.agents.runner import run_resolution_agent
from ai.gena.services.fabula.manager.model.campaigns import CampaignCommand, CreateCampaign, Decide, DecisionSpec, ItemTarget
from ai.gena.services.fabula.manager.model.common import Actor
from ai.gena.services.fabula.manager.model.errors import Conflict, Unprocessable
from ai.gena.services.fabula.manager.model.resolution import LeaseTasks, ProposedResolution, ResolutionPolicy, ReviewProposal, SubmitProposal
from ai.gena.services.fabula.manager.resolution.hint_resolver import HintResolver
from ai.gena.services.fabula.manager.testing.scenarios import SCENARIO, V1, V3, fleet, hold_calls, publish
from ai.gena.services.fabula.manager.testing.stand import AGENT, ALICE, Stand

MIGRATION = '${ . + {owner: "ops"} }'


def run(coroutine):
    return asyncio.run(coroutine)


class Setup:
    """Fabulas blocked on V1 -> V3, delegated to agents under `policy`."""

    def __init__(self, policy: ResolutionPolicy, v1: str = V1, capability: str = "echo:1@test"):
        self.stand = Stand()
        self.stand.world.invoker.behaviours["strict:1@test"] = lambda request: OutcomeOk(output={"y": 1})
        run(publish(self.stand, v1, "1.0.0"))
        hold_calls(self.stand, capability)
        self.ids = run(fleet(self.stand))
        run(publish(self.stand, V3, "3.0.0"))
        self.migrations = self.stand.manager.migrations
        self.resolution = self.stand.manager.resolution
        request = CreateCampaign(request_id="c", scenario=SCENARIO, target_version="3.0.0", policy=policy, context_migration=MIGRATION)
        self.campaign = run(self.migrations.create(ALICE, request))
        decided = run(
            self.migrations.decide(
                ALICE, self.campaign.campaign_id, Decide(request_id="d", decisions=(DecisionSpec(target=ItemTarget(verdicts=("blocked",)), action="delegate_to_agent"),))
            )
        )
        self.task_ids = decided.task_ids

    def task_for(self, role: str):
        return next(t for t in run(self.resolution.list()).items if self.ids[role] in t.fabula_ids)

    def lease(self, actor: Actor = AGENT, **fields):
        return run(self.resolution.lease(actor, LeaseTasks(request_id="lease", **fields))).items

    def submit(self, task_id: str, proposal: ProposedResolution, actor: Actor = AGENT, request_id: str = "s"):
        return run(self.resolution.submit(actor, task_id, SubmitProposal(request_id=request_id, proposal=proposal)))

    def ref(self, role: str) -> str:
        return run(self.stand.runtime.state(self.ids[role])).scenario.ref


RESTART_ASK = ProposedResolution(action="resolve", mapping={"/ask": "/ask"}, context_migration=MIGRATION, rationale="ask changed capability")


def test_delegation_creates_one_task_per_cluster_and_the_brief_explains_the_conflict():
    setup = Setup(ResolutionPolicy(mode="agent_proposes"))
    assert len(setup.task_ids) == 2
    campaign = run(setup.migrations.get(setup.campaign.campaign_id))
    assert campaign.counts["state.delegated"] == 3
    leased = setup.lease(max_tasks=5)
    brief = next(t.brief for t in leased if setup.ids["at_call"] in t.task.fabula_ids)
    assert brief.target_ref == "test/order:3.0.0"
    assert brief.problems == ("'/ask': capability changed from echo:1@test to strict:1@test; map it explicitly to restart it",)
    assert brief.old_sources["/ask"]["call"] == "echo:1@test" and brief.new_sources["/ask"]["call"] == "strict:1@test"
    assert "/finish" in brief.target_nodes and brief.representative.context_keys == ("ticket",)
    assert brief.representative.context is None, "values stay hidden unless the policy exposes them"
    assert brief.proposal_schema["title"] == "ProposedResolution"
    text = render_brief(brief)
    assert "capability changed from echo:1@test to strict:1@test" in text and "## Answer schema" in text


def test_agent_proposes_and_a_person_approves():
    setup = Setup(ResolutionPolicy(mode="agent_proposes"))
    task = setup.task_for("at_call")
    setup.lease(max_tasks=5)
    dry = run(setup.resolution.dry_run(AGENT, task.task_id, RESTART_ASK))
    assert dry.acceptable and {i.verdict for i in dry.items} == {"with_restarts"}
    proposed = setup.submit(task.task_id, RESTART_ASK)
    assert proposed.status == "awaiting_approval" and setup.ref("at_call") == "test/order:1.0.0"
    assert setup.submit(task.task_id, RESTART_ASK) == proposed, "a repeated submission changes nothing"
    approved = run(setup.resolution.approve(ALICE, task.task_id, ReviewProposal(request_id="ok", reason="looks right")))
    assert (approved.status, approved.decided_by) == ("applied", "user:alice")
    assert setup.ref("at_call") == setup.ref("failed") == "test/order:3.0.0"
    actions = setup.stand.audit.actions(f"resolution-task:{task.task_id}")
    assert actions == ["resolution.task_created", "resolution.proposed", "resolution.approved"]
    assert "proposal.awaiting_approval" in setup.stand.notifier.kinds()


def test_agent_applies_without_a_person_when_every_limit_holds():
    setup = Setup(ResolutionPolicy(mode="agent_applies"))
    task = setup.task_for("at_call")
    setup.lease(max_tasks=5)
    applied = setup.submit(task.task_id, RESTART_ASK)
    assert (applied.status, applied.decided_by) == ("applied", "agent:resolver-1")
    items = {i.fabula_id: i for i in run(setup.migrations.items(setup.campaign.campaign_id)).items}
    assert items[setup.ids["at_call"]].state == "migrated"
    assert items[setup.ids["at_call"]].decision.task_id == task.task_id


@pytest.mark.parametrize(
    "policy, proposal, code",
    [
        (ResolutionPolicy(mode="agent_applies"), ProposedResolution(action="cancel_fabula"), "action_not_allowed"),
        (ResolutionPolicy(mode="agent_applies", require_compatible=True), RESTART_ASK, "restarts_not_allowed"),
        (ResolutionPolicy(mode="agent_applies"), ProposedResolution(action="resolve", mapping={"/ask": "/nowhere"}), "not_applicable"),
        (ResolutionPolicy(mode="agent_applies"), RESTART_ASK.model_copy(update={"fabula_ids": ("someone-else",)}), "foreign_items"),
    ],
    ids=["action", "restarts", "inapplicable", "foreign"],
)
def test_proposals_that_break_the_policy_are_refused(policy, proposal, code):
    setup = Setup(policy)
    task = setup.task_for("at_call")
    setup.lease(max_tasks=5)
    with pytest.raises(Unprocessable) as error:
        setup.submit(task.task_id, proposal)
    dry_run = error.value.details["dry_run"]
    codes = [v["code"] for v in dry_run["violations"]] + [v["code"] for i in dry_run["items"] for v in i["violations"]]
    assert code in codes
    assert run(setup.resolution.get(task.task_id)).status == "leased", "the agent may try again"
    assert setup.ref("at_call") == "test/order:1.0.0"
    assert "resolution.refused" in setup.stand.audit.actions(f"resolution-task:{task.task_id}")


def test_cancelling_an_effectful_invocation_is_refused():
    setup = Setup(ResolutionPolicy(mode="agent_applies"), v1=V1.replace("echo:1@test", "slow:1@test"), capability="slow:1@test")
    task = setup.task_for("at_call")
    setup.lease(max_tasks=5)
    dry = run(setup.resolution.dry_run(AGENT, task.task_id, RESTART_ASK))
    assert not dry.acceptable
    [violation] = [v for i in dry.items for v in i.violations if i.fabula_id == setup.ids["at_call"]]
    assert violation.code == "cancels_effectful"
    allowed = Setup(ResolutionPolicy(mode="agent_applies", forbid_cancelling_effectful=False), v1=V1.replace("echo:1@test", "slow:1@test"), capability="slow:1@test")
    task = allowed.task_for("at_call")
    allowed.lease(max_tasks=5)
    assert allowed.submit(task.task_id, RESTART_ASK).status == "applied"


def test_only_allowed_actors_lease_and_leases_expire():
    setup = Setup(ResolutionPolicy(mode="agent_proposes", allowed_actors=("resolver-1",)))
    assert setup.lease(Actor(id="stranger", kind="agent")) == ()
    assert setup.lease(Actor(id="bob")) == ()
    [first] = setup.lease(lease_seconds=60)
    assert setup.lease(Actor(id="resolver-1", kind="agent"), max_tasks=5)[0].task.task_id == first.task.task_id, "the holder renews its lease"
    other = Setup(ResolutionPolicy(mode="agent_proposes"))
    [leased] = other.lease(lease_seconds=60)
    other.stand.clock.advance(timedelta(minutes=2))
    [taken] = other.lease(Actor(id="resolver-2", kind="agent"))
    assert taken.task.task_id == leased.task.task_id and taken.task.attempts == 2
    with pytest.raises(Conflict) as error:
        other.submit(leased.task.task_id, RESTART_ASK)
    assert error.value.code == "lease_required"


def test_rejections_reopen_the_task_or_give_the_items_back():
    setup = Setup(ResolutionPolicy(mode="agent_proposes"))
    task = setup.task_for("at_call")
    setup.lease(max_tasks=5)
    setup.submit(task.task_id, RESTART_ASK)
    reopened = run(setup.resolution.reject(ALICE, task.task_id, ReviewProposal(request_id="no", reason="wrong node")))
    assert (reopened.status, reopened.lease_holder) == ("open", None)
    setup.lease(max_tasks=5)
    setup.submit(task.task_id, ProposedResolution(action="stay", rationale="leave them"), request_id="s2")
    closed = run(setup.resolution.reject(ALICE, task.task_id, ReviewProposal(request_id="no2", reopen=False)))
    assert closed.status == "rejected"
    items = {i.fabula_id: i.state for i in run(setup.migrations.items(setup.campaign.campaign_id)).items}
    assert {items[f] for f in task.fabula_ids} == {"pending"}


def test_an_outdated_proposal_cannot_be_approved():
    setup = Setup(ResolutionPolicy(mode="agent_proposes"))
    task = setup.task_for("at_call")
    setup.lease(max_tasks=5)
    setup.submit(task.task_id, RESTART_ASK.model_copy(update={"mapping": {"/ask": "/ask", "/merged": "/merged"}}))
    run(setup.stand.manager.fabulas.control(ALICE, setup.ids["failed"], _cancel_request()))
    with pytest.raises(Conflict) as error:
        run(setup.resolution.approve(ALICE, task.task_id, ReviewProposal(request_id="ok")))
    assert error.value.code == "proposal_outdated"
    assert run(setup.resolution.get(task.task_id)).status == "open"


def _cancel_request():
    from ai.gena.services.fabula.engine.model.stimuli import Cancel
    from ai.gena.services.fabula.manager.model.fabulas import ControlFabula

    return ControlFabula(request_id="cancel-failed", action=Cancel())


def test_the_hint_resolver_runs_through_the_same_api():
    setup = Setup(ResolutionPolicy(mode="agent_applies"))
    resolver = HintResolver(actor_id=AGENT.id)
    runs = run(run_resolution_agent(setup.resolution, resolver, max_tasks=5, run_id="r1"))
    assert sorted(r.outcome for r in runs) == ["submitted", "submitted"]
    assert {setup.ref(role) for role in ("at_call", "at_event", "failed")} == {"test/order:3.0.0"}
    assert run(setup.migrations.get(setup.campaign.campaign_id)).status == "done"


def test_a_failing_agent_gives_the_task_back():
    setup = Setup(ResolutionPolicy(mode="agent_applies"))

    class Broken:
        actor_id = AGENT.id

        async def resolve(self, brief):
            raise TimeoutError("model timed out")

    runs = run(run_resolution_agent(setup.resolution, Broken(), max_tasks=1, run_id="r1"))
    assert [(r.outcome, r.detail) for r in runs] == [("failed", "TimeoutError('model timed out')")]
    assert run(setup.resolution.get(runs[0].task_id)).status == "open"


def test_cancelling_the_campaign_closes_its_tasks():
    setup = Setup(ResolutionPolicy(mode="agent_proposes"))
    run(setup.migrations.cancel(ALICE, setup.campaign.campaign_id, CampaignCommand(request_id="stop")))
    assert {t.status for t in run(setup.resolution.list(campaign_id=setup.campaign.campaign_id)).items} == {"closed"}
    assert setup.lease(max_tasks=5) == ()


def test_answers_are_parsed_into_proposals():
    answer = 'Here you go:\n```json\n{"action": "resolve", "mapping": {"/ask": "/ask"}, "rationale": "restart"}\n```'
    assert parse_proposal(answer) == ProposedResolution(action="resolve", mapping={"/ask": "/ask"}, rationale="restart")
    with pytest.raises(UnparsableAnswer):
        parse_proposal("no json here")
    with pytest.raises(UnparsableAnswer):
        parse_proposal('{"action": "teleport"}')


class _Crash(Exception):
    pass


def test_an_interrupted_approval_is_finished_by_repeating_it():
    setup = Setup(ResolutionPolicy(mode="agent_proposes"))
    task = setup.task_for("at_call")
    setup.lease(max_tasks=5)
    setup.submit(task.task_id, RESTART_ASK)
    original = setup.migrations.apply

    async def crash_after(*args, **kwargs):
        await original(*args, **kwargs)
        setup.migrations.apply = original
        raise _Crash()

    setup.migrations.apply = crash_after
    with pytest.raises(_Crash):
        run(setup.resolution.approve(ALICE, task.task_id, ReviewProposal(request_id="ok")))
    assert run(setup.resolution.get(task.task_id)).status == "applying"
    assert run(setup.resolution.approve(ALICE, task.task_id, ReviewProposal(request_id="ok"))).status == "applied"
    assert setup.ref("at_call") == "test/order:3.0.0"


def test_a_repeated_submission_under_agent_applies_carries_out_an_interrupted_proposal():
    setup = Setup(ResolutionPolicy(mode="agent_applies"))
    task = setup.task_for("at_call")
    setup.lease(max_tasks=5)
    original = setup.migrations.approve_resolution

    async def crash_once(*args, **kwargs):
        setup.migrations.approve_resolution = original
        raise _Crash()

    setup.migrations.approve_resolution = crash_once
    with pytest.raises(_Crash):
        setup.submit(task.task_id, RESTART_ASK)
    assert setup.submit(task.task_id, RESTART_ASK).status == "applied"
    assert {setup.ref("at_call"), setup.ref("failed")} == {"test/order:3.0.0"}
