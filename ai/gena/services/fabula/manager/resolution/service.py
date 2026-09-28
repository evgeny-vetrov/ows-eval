"""Agentic resolution of migration conflicts.

Delegated items become tasks (one per cluster, split by `policy.max_items`). An agent
leases a task, reads its brief, may dry-run proposals and submits one. Every proposal
is dry-run and checked against the campaign's policy; depending on the mode it is
applied at once (`agent_applies`) or waits for a person (`agent_proposes`).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from ai.gena.services.fabula.engine.ports.storage import FabulaNotFound
from ai.gena.services.fabula.manager.deps import Deps
from ai.gena.services.fabula.manager.diff.service import DiffService
from ai.gena.services.fabula.manager.migrations.service import MigrationService, verdict_of
from ai.gena.services.fabula.manager.model.campaigns import ApplyCampaign, CampaignItem, MigrationCampaign
from ai.gena.services.fabula.manager.model.common import Actor, split_ref
from ai.gena.services.fabula.manager.model.errors import Conflict, Forbidden, NotFound, RevisionConflict, Unprocessable
from ai.gena.services.fabula.manager.model.resolution import (
    AgenticResolutionTask,
    DryRunItem,
    DryRunResult,
    LeasedTask,
    LeasedTasks,
    LeaseTasks,
    ProposedResolution,
    ResolutionPolicy,
    ReviewProposal,
    SubmitProposal,
    TaskList,
    TaskStatus,
    Violation,
)
from ai.gena.services.fabula.manager.resolution.brief import build_brief

_OPEN_ITEM_STATES = ("pending", "delegated")


def may_work(actor: Actor, policy: ResolutionPolicy) -> bool:
    if policy.allowed_actors:
        return actor.id in policy.allowed_actors
    return actor.kind == "agent"


class ResolutionService:
    def __init__(self, deps: Deps, migrations: MigrationService, diffs: DiffService):
        self.deps = deps
        self.migrations = migrations
        self.diffs = diffs

    # --- tasks ----------------------------------------------------------------------

    async def create_tasks(self, actor: Actor, campaign: MigrationCampaign, items: list[CampaignItem], pool: str) -> list[tuple[str, list[CampaignItem]]]:
        clusters: dict[str, list[CampaignItem]] = defaultdict(list)
        for item in items:
            clusters[item.cluster_id].append(item)
        created = []
        size = campaign.policy.max_items
        now = self.deps.clock.now()
        for cluster_id, members in clusters.items():
            for start in range(0, len(members), size):
                chunk = members[start : start + size]
                task = AgenticResolutionTask(
                    task_id=self.deps.ids.new("res"),
                    campaign_id=campaign.campaign_id,
                    cluster_id=cluster_id,
                    fabula_ids=tuple(i.fabula_id for i in chunk),
                    pool=pool,
                    created_at=now,
                    updated_at=now,
                )
                await self.deps.tasks.create(task)
                await self.deps.audit(actor, "resolution.task_created", f"resolution-task:{task.task_id}", campaign=campaign.campaign_id, items=len(chunk))
                await self.deps.notify("resolution.task_open", f"resolution-task:{task.task_id}", pool=pool, campaign=campaign.campaign_id)
                created.append((task.task_id, chunk))
        return created

    async def get(self, task_id: str) -> AgenticResolutionTask:
        task = await self.deps.tasks.get(task_id)
        if task is None:
            raise NotFound(f"resolution task {task_id} does not exist", code="task_not_found")
        return task

    async def list(self, campaign_id: str | None = None, status: TaskStatus | None = None, pool: str | None = None) -> TaskList:
        return TaskList(items=tuple(await self.deps.tasks.list(campaign_id, status, pool)))

    async def _save(self, task: AgenticResolutionTask, **changes) -> AgenticResolutionTask:
        await self.deps.tasks.save(task.model_copy(update={**changes, "updated_at": self.deps.clock.now()}), task.revision)
        return await self.get(task.task_id)

    # --- leases ---------------------------------------------------------------------

    def _lease_free(self, task: AgenticResolutionTask, actor: Actor) -> bool:
        if task.status == "open":
            return True
        return task.status == "leased" and (task.lease_holder == actor.id or task.lease_expires_at <= self.deps.clock.now())

    async def lease(self, actor: Actor, request: LeaseTasks) -> LeasedTasks:
        leased: list[LeasedTask] = []
        expires = self.deps.clock.now() + timedelta(seconds=request.lease_seconds)
        for task in await self.deps.tasks.list(pool=request.pool):
            if len(leased) >= request.max_tasks:
                break
            if not self._lease_free(task, actor):
                continue
            campaign = await self.migrations.get(task.campaign_id)
            if campaign.status in ("paused", "done", "cancelled") or not may_work(actor, campaign.policy):
                continue
            attempts = task.attempts + (0 if task.lease_holder == actor.id and task.status == "leased" else 1)
            try:
                task = await self._save(task, status="leased", lease_holder=actor.id, lease_expires_at=expires, attempts=attempts)
            except RevisionConflict:
                continue
            leased.append(LeasedTask(task=task, brief=await build_brief(self.deps, self.diffs, campaign, task)))
        if leased:
            await self.deps.audit(actor, "resolution.leased", f"pool:{request.pool}", request.request_id, tasks=[t.task.task_id for t in leased])
        return LeasedTasks(items=tuple(leased))

    async def release(self, actor: Actor, task_id: str, request: ReviewProposal) -> AgenticResolutionTask:
        task = await self.get(task_id)
        if task.status != "leased" or task.lease_holder != actor.id:
            return task
        task = await self._save(task, status="open", lease_holder=None, lease_expires_at=None, note=request.reason)
        await self.deps.audit(actor, "resolution.released", f"resolution-task:{task_id}", request.request_id, reason=request.reason)
        return task

    # --- proposals ------------------------------------------------------------------

    async def dry_run(self, actor: Actor, task_id: str, proposal: ProposedResolution) -> DryRunResult:
        task = await self.get(task_id)
        return await self.evaluate(await self.migrations.get(task.campaign_id), task, proposal)

    async def evaluate(self, campaign: MigrationCampaign, task: AgenticResolutionTask, proposal: ProposedResolution) -> DryRunResult:
        """Dry run of a proposal: the swap reports it leads to and the policy limits it breaks."""
        policy = campaign.policy
        violations = []
        if proposal.action not in policy.allowed_actions:
            violations.append(Violation(code="action_not_allowed", message=f"the policy does not allow '{proposal.action}'"))
        ids = proposal.fabula_ids or task.fabula_ids
        foreign = sorted(set(ids) - set(task.fabula_ids))
        if foreign:
            violations.append(Violation(code="foreign_items", message=f"not part of the task: {', '.join(foreign)}"))
        if len(ids) > policy.max_items:
            violations.append(Violation(code="too_many_items", message=f"at most {policy.max_items} items per proposal"))
        items = []
        for fabula_id in [f for f in ids if f in task.fabula_ids]:
            items.append(await self._evaluate_item(campaign, fabula_id, proposal))
        acceptable = not violations and all(not item.violations for item in items)
        return DryRunResult(acceptable=acceptable, items=tuple(items), violations=tuple(violations))

    async def _evaluate_item(self, campaign: MigrationCampaign, fabula_id: str, proposal: ProposedResolution) -> DryRunItem:
        item = await self.deps.campaigns.get_item(campaign.campaign_id, fabula_id)
        if item is None or item.state not in _OPEN_ITEM_STATES:
            state = item.state if item else "missing"
            return DryRunItem(fabula_id=fabula_id, verdict="not_eligible", violations=(Violation(code="item_closed", message=f"the item is {state}", fabula_id=fabula_id),))
        if proposal.action != "resolve":
            return DryRunItem(fabula_id=fabula_id, verdict=item.verdict, report=item.report)
        try:
            report = await self.deps.runtime.analyze_swap(fabula_id, campaign.target_ref, proposal.mapping, proposal.context_migration)
        except FabulaNotFound:
            return DryRunItem(fabula_id=fabula_id, verdict="not_eligible", violations=(Violation(code="fabula_gone", message="the runtime does not know the fabula", fabula_id=fabula_id),))
        found = await self._policy_violations(campaign, fabula_id, item.from_ref, report)
        return DryRunItem(fabula_id=fabula_id, verdict=verdict_of(report), report=report, violations=tuple(found))

    async def _policy_violations(self, campaign: MigrationCampaign, fabula_id: str, from_ref: str, report) -> list[Violation]:
        found = []
        if not report.applicable:
            found.append(Violation(code="not_applicable", message="; ".join(report.problems), fabula_id=fabula_id))
        if campaign.policy.require_compatible and not report.compatible:
            found.append(Violation(code="restarts_not_allowed", message=f"the swap restarts {', '.join(report.restarts)}", fabula_id=fabula_id))
        if campaign.policy.forbid_cancelling_effectful:
            effectful = await self._effectful_cancellations(from_ref, report)
            if effectful:
                found.append(Violation(code="cancels_effectful", message=f"the swap cancels effectful invocations: {', '.join(effectful)}", fabula_id=fabula_id))
        return found

    async def _effectful_cancellations(self, from_ref: str, report) -> list[str]:
        cancelled = [op for op in report.operations if op.disposition == "cancelled" and op.op_kind == "invocation"]
        if not cancelled:
            return []
        source = await self.deps.scenarios.get_version(*split_ref(from_ref))
        graph = self.deps.graph(source.snapshot)
        effectful = []
        for op in cancelled:
            node = graph.nodes.get(op.old_node)
            effects = node.capability.effects if node is not None and node.capability is not None else ()
            if tuple(effects) != ("none",):
                effectful.append(op.op_id)
        return effectful

    async def submit(self, actor: Actor, task_id: str, request: SubmitProposal) -> AgenticResolutionTask:
        task = await self.get(task_id)
        if task.proposal == request.proposal and task.proposed_by == actor.id and task.status in ("awaiting_approval", "applying", "applied"):
            campaign = await self.migrations.get(task.campaign_id)
            # A repeated submission finishes what a crash interrupted.
            if task.status == "applying" or (task.status == "awaiting_approval" and campaign.policy.mode == "agent_applies"):
                return await self._carry_out(actor, task, campaign)
            return task
        if task.status != "leased" or task.lease_holder != actor.id or task.lease_expires_at <= self.deps.clock.now():
            raise Conflict(f"task {task_id} is not leased by {actor.id}", code="lease_required")
        campaign = await self.migrations.get(task.campaign_id)
        if not may_work(actor, campaign.policy):
            raise Forbidden(f"{actor} may not resolve conflicts of migration {campaign.campaign_id}", code="actor_not_allowed")
        result = await self.evaluate(campaign, task, request.proposal)
        if not result.acceptable:
            await self.deps.audit(actor, "resolution.refused", f"resolution-task:{task_id}", request.request_id, dry_run=result.model_dump(mode="json"))
            raise Unprocessable("the proposal breaks the resolution policy", code="proposal_refused", details={"dry_run": result.model_dump(mode="json")})
        task = await self._save(task, proposal=request.proposal, proposed_by=actor.id, dry_run=result, status="awaiting_approval", lease_expires_at=None)
        await self.deps.audit(
            actor,
            "resolution.proposed",
            f"resolution-task:{task_id}",
            request.request_id,
            proposed=request.proposal.action,
            mapping=request.proposal.mapping,
            rationale=request.proposal.rationale,
            confidence=request.proposal.confidence,
        )
        if campaign.policy.mode == "agent_applies":
            return await self._carry_out(actor, task, campaign)
        await self.deps.notify("proposal.awaiting_approval", f"resolution-task:{task_id}", campaign=campaign.campaign_id)
        return task

    async def approve(self, actor: Actor, task_id: str, request: ReviewProposal) -> AgenticResolutionTask:
        task = await self.get(task_id)
        if task.status == "applied":
            return task
        if task.status == "applying":  # an earlier approval was interrupted
            return await self._carry_out(actor, task, await self.migrations.get(task.campaign_id))
        if task.status != "awaiting_approval":
            raise Conflict(f"task {task_id} is {task.status}, not awaiting approval", code="task_not_awaiting_approval")
        campaign = await self.migrations.get(task.campaign_id)
        result = await self.evaluate(campaign, task, task.proposal)
        if not result.acceptable:
            await self._save(task, status="open", lease_holder=None, dry_run=result, note="the proposal no longer passes the dry run")
            raise Conflict("the proposal no longer passes the dry run; the task is open again", code="proposal_outdated", details={"dry_run": result.model_dump(mode="json")})
        await self.deps.audit(actor, "resolution.approved", f"resolution-task:{task_id}", request.request_id, reason=request.reason)
        return await self._carry_out(actor, task, campaign)

    async def reject(self, actor: Actor, task_id: str, request: ReviewProposal) -> AgenticResolutionTask:
        task = await self.get(task_id)
        if task.status in ("rejected", "open") and task.decided_by == str(actor):
            return task
        if task.status != "awaiting_approval":
            raise Conflict(f"task {task_id} is {task.status}, not awaiting approval", code="task_not_awaiting_approval")
        await self.deps.audit(actor, "resolution.rejected", f"resolution-task:{task_id}", request.request_id, reason=request.reason, reopen=request.reopen)
        if request.reopen:
            return await self._save(task, status="open", lease_holder=None, lease_expires_at=None, decided_by=str(actor), note=request.reason)
        task = await self._save(task, status="rejected", decided_by=str(actor), note=request.reason)
        await self.migrations.return_to_decision(task.campaign_id, task.fabula_ids, f"the resolution proposal was rejected: {request.reason}".strip())
        return task

    async def _carry_out(self, actor: Actor, task: AgenticResolutionTask, campaign: MigrationCampaign) -> AgenticResolutionTask:
        """Decide the task's items as proposed and apply them. `applying` marks the task
        first, so that a repeated submit or approve finishes an interrupted run."""
        if task.status != "applying":
            task = await self._save(task, status="applying", decided_by=str(actor))
        proposal = task.proposal
        ids = proposal.fabula_ids or task.fabula_ids
        await self.migrations.approve_resolution(actor, campaign.campaign_id, ids, proposal.action, proposal.mapping, proposal.context_migration, task.task_id)
        todo, refused = [], []
        for fabula_id in ids:
            item = await self.deps.campaigns.get_item(campaign.campaign_id, fabula_id)
            if item is None:
                continue
            if item.state == "applying":
                todo.append(fabula_id)
            elif item.state == "approved":
                # The analysis at approval time must still respect the policy.
                if await self._policy_violations(campaign, fabula_id, item.from_ref, item.approved_report):
                    refused.append(fabula_id)
                else:
                    todo.append(fabula_id)
        note = ""
        if refused:
            await self.migrations.withdraw_approval(campaign.campaign_id, tuple(refused), "the approved swap breaks the resolution policy")
            note = f"returned to a decision: {', '.join(refused)}"
        if todo:
            try:
                await self.migrations.apply(actor, campaign.campaign_id, ApplyCampaign(request_id=f"task-{task.task_id}", fabula_ids=tuple(todo), max_items=len(todo)))
            except Conflict as exc:
                note = f"approved; not applied yet: {exc.message}"
        rest = tuple(f for f in task.fabula_ids if f not in ids)
        if rest:
            await self.migrations.return_to_decision(campaign.campaign_id, rest, "not covered by the resolution proposal")
        return await self._save(await self.get(task.task_id), status="applied", note=note)
