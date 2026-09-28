"""Migration campaigns.

A campaign analyses every candidate fabula with the runtime's `analyze_swap`, sorts the
results into verdicts and clusters, and waits for decisions. Approved items are applied
one by one:

1. an existing outcome of this attempt's swap, or the fabula already being on the
   target, finishes the item without sending anything again;
2. a running or waiting fabula is paused first (the fence), so that it cannot move
   between the check and the swap; a fabula an operator paused stays as it is;
3. the swap is analysed again and must match the approved report;
4. the swap is sent, its outcome read from the journal and compared with the report;
5. the fence is lifted.

Every request id is derived from the campaign, the fabula and the attempt, and each
control is sent only while its outcome is unknown, so repeating `apply` after a crash
continues where it stopped.
"""

from __future__ import annotations

import base64
import hashlib
from collections import Counter, defaultdict

from ai.gena.services.fabula.engine.model.jsonvalue import digest
from ai.gena.services.fabula.engine.model.stimuli import Cancel, ControlAction, Pause, Resume
from ai.gena.services.fabula.engine.model.swap import SwapReport
from ai.gena.services.fabula.engine.ports.storage import FabulaNotFound
from ai.gena.services.fabula.manager.deps import Deps
from ai.gena.services.fabula.manager.diff.service import DiffService
from ai.gena.services.fabula.manager.fabulas.service import FabulaService
from ai.gena.services.fabula.manager.model.campaigns import (
    FINAL_ITEM_STATES,
    ApplyCampaign,
    ApplyResult,
    CampaignCommand,
    CampaignItem,
    CampaignList,
    CampaignSource,
    Cluster,
    ClusterList,
    CreateCampaign,
    Decide,
    DecideResult,
    Decision,
    DecisionSpec,
    ItemPage,
    ItemTarget,
    MigrationCampaign,
    RevertResult,
    Skipped,
    Verdict,
)
from ai.gena.services.fabula.manager.model.common import Actor, split_ref
from ai.gena.services.fabula.manager.model.errors import AlreadyExists, Conflict, InvalidInput, NotFound, RevisionConflict
from ai.gena.services.fabula.manager.model.fabulas import ACTIVE_STATUSES, ControlOutcome, FabulaQuery
from ai.gena.services.fabula.manager.registry.scenarios import ScenarioRegistry

_SEEN_REQUESTS = 256
_CAS_ATTEMPTS = 8
_OPEN_STATES = ("pending", "delegated")
_WORK_STATES = ("approved", "applying")


def same_plan(a: SwapReport | None, b: SwapReport | None) -> bool:
    """Would both reports make the swap do the same thing?"""
    if a is None or b is None:
        return False
    keys = ("applicable", "compatible", "from_ref", "to_ref", "cursor", "operations", "restarts")
    return all(getattr(a, key) == getattr(b, key) for key in keys)


def verdict_of(report: SwapReport | None) -> Verdict:
    if report is None:
        return "busy"
    if report.compatible:
        return "in_flight"
    return "with_restarts" if report.applicable else "blocked"


def cluster_of(from_ref: str, verdict: Verdict, report: SwapReport | None) -> str:
    signature = {
        "from": from_ref,
        "verdict": verdict,
        "cursor": [[m.old_node, m.kind, m.disposition] for m in report.cursor] if report else [],
        "problems": list(report.problems) if report else [],
    }
    return "c-" + digest(signature)[7:19]


def _matches(item: CampaignItem, target: ItemTarget) -> bool:
    return item.cluster_id in target.cluster_ids or item.fabula_id in target.fabula_ids or item.verdict in target.verdicts


class MigrationService:
    def __init__(self, deps: Deps, registry: ScenarioRegistry, fabulas: FabulaService, diffs: DiffService):
        self.deps = deps
        self.registry = registry
        self.fabulas = fabulas
        self.diffs = diffs
        # Set by the manager: creates agentic resolution tasks for delegated items.
        self.delegate = None

    # --- reading --------------------------------------------------------------------

    async def get(self, campaign_id: str) -> MigrationCampaign:
        campaign = await self.deps.campaigns.get(campaign_id)
        if campaign is None:
            raise NotFound(f"migration {campaign_id} does not exist", code="migration_not_found")
        return campaign

    async def list(self, scenario: str | None = None, status: str | None = None) -> CampaignList:
        found = await self.deps.campaigns.list(scenario)
        return CampaignList(items=tuple(c for c in found if status is None or c.status == status))

    async def items(
        self,
        campaign_id: str,
        verdict: str | None = None,
        state: str | None = None,
        cluster_id: str | None = None,
        page_size: int = 100,
        page_token: str | None = None,
    ) -> ItemPage:
        await self.get(campaign_id)
        found = [
            i
            for i in await self.deps.campaigns.list_items(campaign_id)
            if (verdict is None or i.verdict == verdict) and (state is None or i.state == state) and (cluster_id is None or i.cluster_id == cluster_id)
        ]
        start = _decode_offset(page_token)
        page = found[start : start + page_size]
        token = _encode_offset(start + page_size) if start + page_size < len(found) else None
        return ItemPage(items=tuple(page), next_page_token=token)

    async def clusters(self, campaign_id: str) -> ClusterList:
        campaign = await self.get(campaign_id)
        groups: dict[str, list[CampaignItem]] = defaultdict(list)
        for item in await self.deps.campaigns.list_items(campaign_id):
            groups[item.cluster_id].append(item)
        clusters = []
        for cluster_id, members in groups.items():
            first = members[0]
            report = first.report
            clusters.append(
                Cluster(
                    cluster_id=cluster_id,
                    from_ref=first.from_ref,
                    verdict=first.verdict,
                    cursor=report.cursor if report else (),
                    problems=report.problems if report else (),
                    missing_variables=report.missing_variables if report else (),
                    count=len(members),
                    states=dict(Counter(m.state for m in members)),
                    sample=tuple(m.fabula_id for m in members[:5]),
                    mapping=campaign.mapping_for(first.from_ref),
                )
            )
        return ClusterList(items=tuple(clusters))

    # --- creation -------------------------------------------------------------------

    async def create(self, actor: Actor, request: CreateCampaign, origin: str = "manual", reverts: str | None = None) -> MigrationCampaign:
        campaign_id = "mig-" + hashlib.sha256(f"{request.scenario}|{request.request_id}".encode("utf-8")).hexdigest()[:20]
        fingerprint = digest(request.model_dump(mode="json"))
        existing = await self.deps.campaigns.get(campaign_id)
        if existing is not None:
            return await self._replayed(existing, fingerprint)
        info = await self.registry.get_scenario(request.scenario)
        # Going back is allowed to a deprecated version: running fabulas may still use it.
        pick = self.registry.get_version if origin == "revert" else self.registry.active_version
        target = await pick(request.scenario, request.target_version)
        now = self.deps.clock.now()
        campaign = MigrationCampaign(
            campaign_id=campaign_id,
            scenario=request.scenario,
            target_version=target.version,
            target_ref=target.ref,
            source=request.source,
            mapping=request.mapping,
            use_suggested_mapping=request.use_suggested_mapping,
            context_migration=request.context_migration,
            policy=request.policy or info.resolution_policy,
            auto_apply_compatible=request.auto_apply_compatible,
            batch_size=request.batch_size,
            origin=origin,
            reverts=reverts,
            reason=request.reason,
            request_id=request.request_id,
            request_fingerprint=fingerprint,
            created_by=str(actor),
            created_at=now,
            updated_at=now,
        )
        try:
            await self.deps.campaigns.create(campaign)
        except AlreadyExists:
            return await self._replayed(await self.get(campaign_id), fingerprint)
        await self.deps.audit(actor, "migration.created", f"migration:{campaign_id}", request.request_id, target=target.ref, origin=origin)
        return await self._first_analysis(campaign_id)

    async def _first_analysis(self, campaign_id: str) -> MigrationCampaign:
        await self._discover(campaign_id)
        campaign = await self.get(campaign_id)
        await self._save_campaign(campaign, analyzed_at=self.deps.clock.now())
        campaign = await self._settle(campaign_id)
        if campaign.status == "awaiting_decision":
            await self.deps.notify("campaign.awaiting_decision", f"migration:{campaign_id}", counts=campaign.counts)
        return campaign

    async def _replayed(self, existing: MigrationCampaign, fingerprint: str) -> MigrationCampaign:
        if existing.request_fingerprint != fingerprint:
            raise Conflict(f"request id was already used for migration {existing.campaign_id}", code="request_mismatch")
        if existing.analyzed_at is None:  # the first attempt stopped before its analysis finished
            return await self._first_analysis(existing.campaign_id)
        return existing

    async def propose_for_tag(self, actor: Actor, scenario: str, tag: str, version: str, request_id: str) -> str | None:
        """A tag moved: ask about the running fabulas it routed to other versions."""
        records = await self.fabulas.all(FabulaQuery(scenario=scenario, statuses=ACTIVE_STATUSES, tag=tag))
        if not any(r.version != version for r in records):
            return None
        request = CreateCampaign(
            request_id=f"tag:{tag}:{request_id}"[:128],
            scenario=scenario,
            target_version=version,
            source=CampaignSource(tag=tag),
            reason=f"tag '{tag}' moved to {version}",
        )
        return (await self.create(actor, request, origin="tag_move")).campaign_id

    # --- analysis -------------------------------------------------------------------

    async def _discover(self, campaign_id: str) -> None:
        campaign = await self.get(campaign_id)
        source = campaign.source
        query = FabulaQuery(scenario=campaign.scenario, versions=source.versions, statuses=source.statuses, tag=source.tag, fabula_ids=source.fabula_ids)
        records = [r for r in await self.fabulas.all(query) if r.version != campaign.target_version]
        await self._suggest(campaign_id, {r.ref for r in records})
        campaign = await self.get(campaign_id)
        now = self.deps.clock.now()
        for record in records:
            item = await self.deps.campaigns.get_item(campaign_id, record.fabula_id)
            if item is None:
                holder = await self.deps.campaigns.claim(record.fabula_id, campaign_id)
                blocked = holder != campaign_id
                item = CampaignItem(
                    campaign_id=campaign_id,
                    fabula_id=record.fabula_id,
                    from_ref=record.ref,
                    verdict="not_eligible" if blocked else "busy",
                    state="not_eligible" if blocked else "pending",
                    cluster_id=f"claimed:{holder}" if blocked else "",
                    note=f"the fabula takes part in migration {holder}" if blocked else "",
                    updated_at=now,
                )
                await self.deps.campaigns.put_item(item, None)
                if blocked:
                    continue
            if item.state in ("pending", "approved", "delegated"):
                await self._analyze(campaign, item)

    async def _suggest(self, campaign_id: str, refs: set[str]) -> None:
        campaign = await self.get(campaign_id)
        missing = sorted(refs - set(campaign.suggested_mappings)) if campaign.use_suggested_mapping else []
        if not missing:
            return
        target = await self.registry.get_version(campaign.scenario, campaign.target_version)
        suggested = dict(campaign.suggested_mappings)
        for ref in missing:
            source = await self.deps.scenarios.get_version(*split_ref(ref))
            suggested[ref] = self.diffs.between(source, target).suggested_mapping if source is not None else {}
        await self._save_campaign(campaign, suggested_mappings=suggested)

    def _effective(self, campaign: MigrationCampaign, item: CampaignItem, from_ref: str) -> tuple[dict[str, str], str | None]:
        if item.mapping is not None:
            return item.mapping, item.context_migration
        return campaign.mapping_for(from_ref), campaign.context_migration

    async def _analyze(self, campaign: MigrationCampaign, item: CampaignItem) -> CampaignItem:
        """Fresh analysis of one item against the stored state of its fabula."""
        try:
            state = await self.deps.runtime.state(item.fabula_id)
        except FabulaNotFound:
            return await self._finish(item, "not_eligible", note="the runtime does not know the fabula")
        if state.status in ("completed", "cancelled"):
            return await self._finish(item, "not_eligible", note=f"the fabula is {state.status}")
        if state.scenario.ref == campaign.target_ref:
            return await self._finish(item, "not_eligible", note="the fabula is already on the target version")
        from_ref = state.scenario.ref
        if from_ref not in campaign.suggested_mappings and campaign.use_suggested_mapping:
            await self._suggest(campaign.campaign_id, {from_ref})
            campaign = await self.get(campaign.campaign_id)
        report = None
        if not state.agenda:
            mapping, migration = self._effective(campaign, item, from_ref)
            report = await self.deps.runtime.analyze_swap(item.fabula_id, campaign.target_ref, mapping, migration)
        verdict = verdict_of(report)
        changes = dict(from_ref=from_ref, verdict=verdict, report=report, cluster_id=cluster_of(from_ref, verdict, report), note="")
        movable = verdict in ("in_flight", "with_restarts")
        if item.state == "approved" and not (movable and same_plan(report, item.approved_report)):
            changes.update(state="pending", approved_report=None, note="the analysis changed since the approval")
        elif item.state == "pending" and verdict == "in_flight" and campaign.auto_apply_compatible:
            changes.update(state="approved", approved_report=report)
        return await self._save_item(item, **changes)

    async def refresh(self, actor: Actor, campaign_id: str, request: CampaignCommand) -> MigrationCampaign:
        campaign = await self.get(campaign_id)
        self._require_open(campaign)
        await self._discover(campaign_id)
        await self._save_campaign(await self.get(campaign_id), analyzed_at=self.deps.clock.now())
        await self.deps.audit(actor, "migration.refreshed", f"migration:{campaign_id}", request.request_id)
        return await self._settle(campaign_id)

    # --- decisions ------------------------------------------------------------------

    async def decide(self, actor: Actor, campaign_id: str, request: Decide) -> DecideResult:
        campaign = await self.get(campaign_id)
        if request.request_id in campaign.seen_requests:
            return DecideResult(campaign_id=campaign_id, replayed=True)
        self._require_open(campaign)
        if request.expected_revision is not None and request.expected_revision != campaign.revision:
            raise RevisionConflict(f"migration {campaign_id} is at revision {campaign.revision}, not {request.expected_revision}")
        for spec in request.decisions:
            if spec.action == "delegate_to_agent" and campaign.policy.mode == "manual":
                raise Conflict("the resolution policy is manual: conflicts are not delegated to agents", code="policy_manual")
            if spec.action == "resolve" and spec.mapping is None and spec.context_migration is None:
                raise InvalidInput("resolve needs a mapping or a context migration", code="resolve_empty")
        affected: Counter = Counter()
        skipped: list[Skipped] = []
        task_ids: list[str] = []
        for spec in request.decisions:
            delegated: list[CampaignItem] = []
            for item in [i for i in await self.deps.campaigns.list_items(campaign_id) if _matches(i, spec.target)]:
                if item.state in FINAL_ITEM_STATES or item.state == "applying":
                    skipped.append(Skipped(fabula_id=item.fabula_id, reason=f"the item is {item.state}"))
                    continue
                if spec.action == "delegate_to_agent" and item.state == "delegated":
                    skipped.append(Skipped(fabula_id=item.fabula_id, reason=f"the item is already delegated to {item.task_id}"))
                    continue
                decision = Decision(action=spec.action, actor=str(actor), at=self.deps.clock.now(), request_id=request.request_id, reason=spec.reason)
                item, reason = await self._decide_item(actor, campaign, item, spec, decision)
                if reason:
                    skipped.append(Skipped(fabula_id=item.fabula_id, reason=reason))
                    continue
                if spec.action == "delegate_to_agent":
                    delegated.append(item)
                else:
                    affected[item.state] += 1
            if delegated:
                for task_id, members in await self.delegate(actor, campaign, delegated, spec.pool):
                    task_ids.append(task_id)
                    for member in members:
                        await self._save_item(member, state="delegated", task_id=task_id)
                        affected["delegated"] += 1
        campaign = await self.get(campaign_id)
        await self._save_campaign(campaign, seen_requests=(*campaign.seen_requests, request.request_id)[-_SEEN_REQUESTS:])
        await self.deps.audit(
            actor, "migration.decided", f"migration:{campaign_id}", request.request_id, decisions=[s.model_dump(mode="json") for s in request.decisions]
        )
        await self._settle(campaign_id)
        return DecideResult(campaign_id=campaign_id, affected=dict(affected), skipped=tuple(skipped), task_ids=tuple(task_ids))

    async def _decide_item(self, actor: Actor, campaign: MigrationCampaign, item: CampaignItem, spec: DecisionSpec, decision: Decision) -> tuple[CampaignItem, str]:
        action = spec.action
        if action == "migrate":
            if item.verdict not in ("in_flight", "with_restarts") or item.report is None:
                return item, f"the verdict is {item.verdict}"
            return await self._save_item(item, state="approved", approved_report=item.report, decision=decision), ""
        if action == "stay":
            return await self._finish(item, "staying", decision=decision), ""
        if action == "resolve":
            mapping, migration = self._effective(campaign, item, item.from_ref)
            item = await self._save_item(
                item,
                mapping=spec.mapping if spec.mapping is not None else mapping,
                context_migration=spec.context_migration if spec.context_migration is not None else migration,
                decision=decision,
                state="pending",
                approved_report=None,
            )
            item = await self._analyze(campaign, item)
            if spec.approve and item.state == "pending" and item.verdict in ("in_flight", "with_restarts"):
                item = await self._save_item(item, state="approved", approved_report=item.report)
            return item, ""
        if action == "cancel_fabula":
            outcome = await self._control_once(actor, item.fabula_id, f"mig:{campaign.campaign_id}:{item.fabula_id}:cancel", spec.reason, Cancel())
            if outcome is None or not outcome.accepted:
                return item, f"cancel was not accepted ({outcome.code if outcome else 'no outcome yet'})"
            return await self._finish(item, "cancelled", decision=decision), ""
        return await self._save_item(item, decision=decision), ""

    async def approve_resolution(self, actor: Actor, campaign_id: str, fabula_ids: tuple[str, ...], action: str, mapping: dict, context_migration: str | None, task_id: str) -> list[CampaignItem]:
        """Apply the decision of an accepted resolution proposal to its items."""
        campaign = await self.get(campaign_id)
        results = []
        for fabula_id in fabula_ids:
            item = await self.deps.campaigns.get_item(campaign_id, fabula_id)
            if item is None or item.state in FINAL_ITEM_STATES or item.state == "applying":
                continue
            decision = Decision(action=action, actor=str(actor), at=self.deps.clock.now(), request_id=f"task:{task_id}", task_id=task_id)
            spec = DecisionSpec(target=ItemTarget(fabula_ids=(fabula_id,)), action=action, mapping=mapping, context_migration=context_migration, approve=True)
            item, _ = await self._decide_item(actor, campaign, item, spec, decision)
            results.append(item)
        await self._settle(campaign_id)
        return results

    async def withdraw_approval(self, campaign_id: str, fabula_ids: tuple[str, ...], note: str) -> None:
        for fabula_id in fabula_ids:
            item = await self.deps.campaigns.get_item(campaign_id, fabula_id)
            if item is not None and item.state == "approved":
                await self._save_item(item, state="pending", approved_report=None, task_id=None, note=note)
        await self._settle(campaign_id)

    async def return_to_decision(self, campaign_id: str, fabula_ids: tuple[str, ...], note: str) -> None:
        for fabula_id in fabula_ids:
            item = await self.deps.campaigns.get_item(campaign_id, fabula_id)
            if item is not None and item.state == "delegated":
                await self._save_item(item, state="pending", task_id=None, note=note)
        await self._settle(campaign_id)

    # --- application ----------------------------------------------------------------

    async def apply(self, actor: Actor, campaign_id: str, request: ApplyCampaign) -> ApplyResult:
        campaign = await self.get(campaign_id)
        if campaign.status == "paused":
            raise Conflict(f"migration {campaign_id} is paused", code="migration_paused")
        self._require_open(campaign)
        todo = [
            i
            for i in await self.deps.campaigns.list_items(campaign_id)
            if i.state in _WORK_STATES and (not request.fabula_ids or i.fabula_id in request.fabula_ids)
        ]
        processed: Counter = Counter()
        for item in todo[: request.max_items or campaign.batch_size]:
            processed[(await self._apply_item(actor, campaign, item)).state] += 1
        await self.deps.audit(actor, "migration.applied", f"migration:{campaign_id}", request.request_id, processed=dict(processed))
        campaign = await self._settle(campaign_id)
        if processed.get("pending"):
            await self.deps.notify("campaign.awaiting_decision", f"migration:{campaign_id}", counts=campaign.counts)
        return ApplyResult(campaign=campaign, processed=dict(processed))

    async def _apply_item(self, actor: Actor, campaign: MigrationCampaign, item: CampaignItem) -> CampaignItem:
        fabula_id, target = item.fabula_id, campaign.target_ref
        base = f"mig:{campaign.campaign_id}:{fabula_id}:{item.attempt}"
        if item.state == "approved":
            item = await self._save_item(item, state="applying")
        outcome = await self.fabulas.find_outcome(fabula_id, base + ":swap")
        if outcome is not None:
            return await self._swap_done(actor, item, base, outcome)
        try:
            state = await self.deps.runtime.state(fabula_id)
        except FabulaNotFound:
            return await self._finish(item, "not_eligible", note="the runtime does not know the fabula")
        if state.scenario.ref == target:
            item = await self._unfence(actor, item, base)
            return await self._finish(item, "migrated", note="the fabula was already on the target version")
        if state.status in ("completed", "cancelled"):
            item = await self._unfence(actor, item, base)
            return await self._finish(item, "not_eligible", note=f"the fabula is {state.status}")
        if item.fence == "none" and state.status in ("running", "waiting"):
            item = await self._save_item(item, fence="pausing")
        if item.fence == "pausing":
            pause = await self._control_once(actor, fabula_id, base + ":pause", "migration fence", Pause())
            if pause is None:
                return item  # not applied yet: the next `apply` continues here
            item = await self._save_item(item, fence="paused" if pause.accepted else "none")
            state = await self.deps.runtime.state(fabula_id)
        if state.agenda:
            item = await self._unfence(actor, item, base)
            return await self._back_to_decision(item, None, "the fabula has pending work")
        mapping, migration = self._effective(campaign, item, state.scenario.ref)
        report = await self.deps.runtime.analyze_swap(fabula_id, target, mapping, migration)
        if not same_plan(report, item.approved_report):
            item = await self._unfence(actor, item, base)
            return await self._back_to_decision(item, report, "the analysis changed since the approval")
        await self.deps.runtime.swap_version(fabula_id, base + ":swap", str(actor), f"migration {campaign.campaign_id}", target, mapping, migration)
        outcome = await self.fabulas.find_outcome(fabula_id, base + ":swap")
        if outcome is None:
            return item
        return await self._swap_done(actor, item, base, outcome)

    async def _swap_done(self, actor: Actor, item: CampaignItem, base: str, outcome: ControlOutcome) -> CampaignItem:
        item = await self._unfence(actor, item, base)
        if not outcome.accepted:
            return await self._back_to_decision(item, outcome.report, f"the swap was rejected: {outcome.code} {outcome.message}".strip())
        diverged = outcome.report is not None and not same_plan(outcome.report, item.approved_report)
        note = "the executed swap differs from the approved report" if diverged else ""
        return await self._finish(item, "diverged" if diverged else "migrated", report=outcome.report or item.report, note=note)

    async def _control_once(self, actor: Actor, fabula_id: str, request_id: str, reason: str, action: ControlAction) -> ControlOutcome | None:
        outcome = await self.fabulas.find_outcome(fabula_id, request_id)
        if outcome is None:
            await self.deps.runtime.control(fabula_id, request_id, str(actor), reason, action)
            outcome = await self.fabulas.find_outcome(fabula_id, request_id)
        return outcome

    async def _unfence(self, actor: Actor, item: CampaignItem, base: str) -> CampaignItem:
        paused = item.fence == "paused"
        if item.fence == "pausing":  # the pause may have reached the fabula before a crash
            pause = await self.fabulas.find_outcome(item.fabula_id, base + ":pause")
            paused = pause is not None and pause.accepted
        if paused:
            await self._control_once(actor, item.fabula_id, base + ":resume", "migration fence", Resume())
        if item.fence != "none":
            item = await self._save_item(item, fence="none")
        return item

    async def _back_to_decision(self, item: CampaignItem, report: SwapReport | None, note: str) -> CampaignItem:
        verdict = verdict_of(report)
        return await self._save_item(
            item,
            state="pending",
            verdict=verdict,
            report=report,
            cluster_id=cluster_of(item.from_ref, verdict, report),
            approved_report=None,
            attempt=item.attempt + 1,
            note=note,
        )

    # --- campaign life cycle ----------------------------------------------------------

    async def pause(self, actor: Actor, campaign_id: str, request: CampaignCommand) -> MigrationCampaign:
        campaign = await self.get(campaign_id)
        self._require_open(campaign)
        await self._save_campaign(campaign, status="paused")
        await self.deps.audit(actor, "migration.paused", f"migration:{campaign_id}", request.request_id, reason=request.reason)
        return await self.get(campaign_id)

    async def resume(self, actor: Actor, campaign_id: str, request: CampaignCommand) -> MigrationCampaign:
        campaign = await self.get(campaign_id)
        if campaign.status != "paused":
            return campaign
        await self._save_campaign(campaign, status="awaiting_decision")
        await self.deps.audit(actor, "migration.resumed", f"migration:{campaign_id}", request.request_id, reason=request.reason)
        return await self._settle(campaign_id)

    async def cancel(self, actor: Actor, campaign_id: str, request: CampaignCommand) -> MigrationCampaign:
        """Stop the campaign; every fabula it did not move stays on its version."""
        campaign = await self.get(campaign_id)
        if campaign.status == "cancelled":
            return campaign
        for item in await self.deps.campaigns.list_items(campaign_id):
            if item.state not in FINAL_ITEM_STATES:
                await self._stop_item(actor, item)
        for task in await self.deps.tasks.list(campaign_id=campaign_id):
            if task.status in ("open", "leased", "awaiting_approval"):
                await self.deps.tasks.save(task.model_copy(update={"status": "closed", "note": "the migration was cancelled", "updated_at": self.deps.clock.now()}), task.revision)
        campaign = await self.get(campaign_id)
        await self._save_campaign(campaign, status="cancelled")
        await self.deps.audit(actor, "migration.cancelled", f"migration:{campaign_id}", request.request_id, reason=request.reason)
        return await self._settle(campaign_id)

    async def _stop_item(self, actor: Actor, item: CampaignItem) -> CampaignItem:
        base = f"mig:{item.campaign_id}:{item.fabula_id}:{item.attempt}"
        if item.state == "applying":
            outcome = await self.fabulas.find_outcome(item.fabula_id, base + ":swap")
            if outcome is not None:
                item = await self._swap_done(actor, item, base, outcome)
                if item.state in FINAL_ITEM_STATES:
                    return item
        item = await self._unfence(actor, item, base)
        return await self._finish(item, "staying", note="the migration was cancelled")

    async def revert(self, actor: Actor, campaign_id: str, request: CampaignCommand) -> RevertResult:
        """Campaigns that move the fabulas this one migrated back to their versions."""
        campaign = await self.get(campaign_id)
        moved: dict[tuple[str, tuple], list[str]] = defaultdict(list)
        for item in await self.deps.campaigns.list_items(campaign_id):
            if item.state in ("migrated", "diverged"):
                mapping = self._effective(campaign, item, item.from_ref)[0]
                moved[(item.from_ref, tuple(sorted(mapping.items())))].append(item.fabula_id)
        created = []
        for (from_ref, mapping), fabula_ids in sorted(moved.items()):
            # Inverted, identity entries included: they are what restarts changed nodes.
            inverse: dict[str, str] = {}
            for old, new in mapping:
                inverse.setdefault(new, old)
            revert = CreateCampaign(
                request_id=hashlib.sha256(f"{request.request_id}|{from_ref}|{mapping}".encode("utf-8")).hexdigest()[:40],
                scenario=campaign.scenario,
                target_version=split_ref(from_ref)[1],
                source=CampaignSource(fabula_ids=tuple(fabula_ids)),
                mapping=inverse,
                policy=campaign.policy,
                reason=request.reason or f"revert of {campaign_id}",
            )
            created.append((await self.create(actor, revert, origin="revert", reverts=campaign_id)).campaign_id)
        await self.deps.audit(actor, "migration.reverted", f"migration:{campaign_id}", request.request_id, campaigns=created)
        return RevertResult(campaign_ids=tuple(created))

    @staticmethod
    def _require_open(campaign: MigrationCampaign) -> None:
        if campaign.status in ("done", "cancelled"):
            raise Conflict(f"migration {campaign.campaign_id} is {campaign.status}", code="migration_closed")

    async def _settle(self, campaign_id: str) -> MigrationCampaign:
        for _ in range(_CAS_ATTEMPTS):
            campaign = await self.get(campaign_id)
            items = await self.deps.campaigns.list_items(campaign_id)
            counts = {f"state.{k}": v for k, v in sorted(Counter(i.state for i in items).items())}
            counts.update({f"verdict.{k}": v for k, v in sorted(Counter(i.verdict for i in items).items())})
            status = campaign.status
            if status not in ("paused", "cancelled"):
                if any(i.state in _OPEN_STATES for i in items):
                    status = "awaiting_decision"
                elif any(i.state in _WORK_STATES for i in items):
                    status = "ready"
                else:
                    status = "done"
            if status in ("done", "cancelled"):
                for item in items:
                    await self.deps.campaigns.release(item.fabula_id, campaign_id)
            await self._close_orphan_tasks(campaign_id, items)
            if counts == campaign.counts and status == campaign.status:
                return campaign
            try:
                await self._save_campaign(campaign, counts=counts, status=status)
            except RevisionConflict:
                continue
            return await self.get(campaign_id)
        raise RevisionConflict(f"migration {campaign_id} keeps changing")

    async def _close_orphan_tasks(self, campaign_id: str, items: list[CampaignItem]) -> None:
        """Close resolution tasks none of whose fabulas is still delegated to them."""
        delegated = {(i.fabula_id, i.task_id) for i in items if i.state == "delegated"}
        for task in await self.deps.tasks.list(campaign_id=campaign_id):
            if task.status not in ("open", "leased", "awaiting_approval"):
                continue
            if any((fabula_id, task.task_id) in delegated for fabula_id in task.fabula_ids):
                continue
            closed = task.model_copy(update={"status": "closed", "note": "its fabulas were decided elsewhere", "updated_at": self.deps.clock.now()})
            try:
                await self.deps.tasks.save(closed, task.revision)
            except RevisionConflict:
                pass

    # --- persistence helpers ----------------------------------------------------------

    async def _save_campaign(self, campaign: MigrationCampaign, **changes) -> None:
        await self.deps.campaigns.save(campaign.model_copy(update={**changes, "updated_at": self.deps.clock.now()}), campaign.revision)

    async def _save_item(self, item: CampaignItem, **changes) -> CampaignItem:
        await self.deps.campaigns.put_item(item.model_copy(update={**changes, "updated_at": self.deps.clock.now()}), item.revision)
        return await self.deps.campaigns.get_item(item.campaign_id, item.fabula_id)

    async def _finish(self, item: CampaignItem, state: str, **changes) -> CampaignItem:
        verdict = "not_eligible" if state == "not_eligible" else item.verdict
        item = await self._save_item(item, state=state, verdict=verdict, fence="none", **changes)
        await self.deps.campaigns.release(item.fabula_id, item.campaign_id)
        return item


def _encode_offset(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii")


def _decode_offset(token: str | None) -> int:
    if not token:
        return 0
    try:
        return max(0, int(base64.urlsafe_b64decode(token.encode("ascii")).decode("ascii")))
    except ValueError:
        raise InvalidInput("page_token is malformed", code="bad_page_token") from None
