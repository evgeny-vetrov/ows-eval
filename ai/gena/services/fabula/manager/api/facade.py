"""The API as async methods, one per operation: `(actor, path, query, body) -> result`.

Transport-agnostic: `dispatch` validates the input into these arguments and serializes
the result; an HTTP adapter only maps requests to `dispatch`.
"""

from typing import Any

from ai.gena.services.fabula.manager.api.schemas import (
    ApiResult,
    AuditQuery,
    CapabilityList,
    CatalogQuery,
    DiffQuery,
    EventTypeList,
    FabulaSearch,
    ImportFabula,
    ItemQuery,
    MigrationQuery,
    NamespaceQuery,
    ReconcileResult,
    RoutingQuery,
    ScenarioFilter,
    TaskQuery,
)
from ai.gena.services.fabula.manager.model.audit import AuditPage
from ai.gena.services.fabula.manager.model.campaigns import ApplyCampaign, CampaignCommand, CreateCampaign, Decide
from ai.gena.services.fabula.manager.model.common import Actor, scenario_id
from ai.gena.services.fabula.manager.model.errors import InvalidInput
from ai.gena.services.fabula.manager.model.fabulas import AnalyzeSwap, ControlFabula, Selector, StartFabula
from ai.gena.services.fabula.manager.model.resolution import DryRunProposal, LeaseTasks, ReviewProposal, SetResolutionPolicy, SubmitProposal
from ai.gena.services.fabula.manager.model.scenarios import CreateScenario, DeprecateVersion, PublishVersion, UpdateScenario, ValidateScenario
from ai.gena.services.fabula.manager.model.tags import MoveTag, RollbackTag, SetAllocation
from ai.gena.services.fabula.manager.service import Manager

Path = dict[str, str]


def _scenario(path: Path) -> str:
    return scenario_id(path["namespace"], path["name"])


class FabulaApi:
    def __init__(self, manager: Manager):
        self.manager = manager
        self.registry = manager.registry
        self.tags = manager.tags
        self.fabulas = manager.fabulas
        self.migrations = manager.migrations
        self.resolution = manager.resolution

    # --- scenarios ------------------------------------------------------------------

    async def list_scenarios(self, actor: Actor, path: Path, query: NamespaceQuery, body: None):
        return await self.registry.list_scenarios(query.namespace)

    async def create_scenario(self, actor: Actor, path: Path, query: None, body: CreateScenario):
        return await self.registry.create_scenario(actor, body)

    async def get_scenario(self, actor: Actor, path: Path, query: None, body: None):
        return await self.registry.get_scenario(_scenario(path))

    async def update_scenario(self, actor: Actor, path: Path, query: None, body: UpdateScenario):
        return await self.registry.update_scenario(actor, _scenario(path), body)

    async def set_resolution_policy(self, actor: Actor, path: Path, query: None, body: SetResolutionPolicy):
        return await self.registry.set_policy(actor, _scenario(path), body)

    async def validate_scenario(self, actor: Actor, path: Path, query: None, body: ValidateScenario):
        return self.registry.validate(body.source)

    async def publish_version(self, actor: Actor, path: Path, query: None, body: PublishVersion):
        return await self.registry.publish(actor, _scenario(path), body)

    async def list_versions(self, actor: Actor, path: Path, query: None, body: None):
        return await self.registry.list_versions(_scenario(path))

    async def get_version(self, actor: Actor, path: Path, query: None, body: None):
        return await self.registry.get_version(_scenario(path), path["version"])

    async def deprecate_version(self, actor: Actor, path: Path, query: None, body: DeprecateVersion):
        return await self.registry.deprecate(actor, _scenario(path), path["version"], body)

    async def diff_versions(self, actor: Actor, path: Path, query: DiffQuery, body: None):
        return await self.manager.diffs.diff(_scenario(path), query.from_version, query.to_version)

    async def list_capabilities(self, actor: Actor, path: Path, query: CatalogQuery, body: None):
        return CapabilityList(items=tuple(await self.registry.capabilities(query.q)))

    async def list_event_types(self, actor: Actor, path: Path, query: CatalogQuery, body: None):
        return EventTypeList(items=tuple(await self.registry.event_types(query.q)))

    # --- tags -----------------------------------------------------------------------

    async def list_tags(self, actor: Actor, path: Path, query: None, body: None):
        return await self.tags.list(_scenario(path))

    async def get_tag(self, actor: Actor, path: Path, query: None, body: None):
        return await self.tags.get(_scenario(path), path["tag"])

    async def move_tag(self, actor: Actor, path: Path, query: None, body: MoveTag):
        return await self.tags.move(actor, _scenario(path), path["tag"], body)

    async def allocate_tag(self, actor: Actor, path: Path, query: None, body: SetAllocation):
        return await self.tags.allocate(actor, _scenario(path), path["tag"], body)

    async def rollback_tag(self, actor: Actor, path: Path, query: None, body: RollbackTag):
        return await self.tags.rollback(actor, _scenario(path), path["tag"], body)

    async def get_tag_history(self, actor: Actor, path: Path, query: None, body: None):
        return await self.tags.history(_scenario(path), path["tag"])

    async def resolve_tag(self, actor: Actor, path: Path, query: RoutingQuery, body: None):
        assignment, _ = await self.tags.route(_scenario(path), Selector(tag=path["tag"]), query.routing_key)
        return assignment

    # --- fabulas --------------------------------------------------------------------

    async def start_fabula(self, actor: Actor, path: Path, query: None, body: StartFabula):
        result = await self.fabulas.start(actor, body)
        return ApiResult(201 if result.created else 200, result)

    async def search_fabulas(self, actor: Actor, path: Path, query: FabulaSearch, body: None):
        try:
            search = query.to_query()
        except ValueError as exc:
            raise InvalidInput(str(exc), code="bad_label") from None
        return await self.fabulas.query(search)

    async def count_fabulas(self, actor: Actor, path: Path, query: ScenarioFilter, body: None):
        return await self.fabulas.counts(query.scenario)

    async def reconcile_fabulas(self, actor: Actor, path: Path, query: None, body: None):
        return ReconcileResult(repaired=await self.fabulas.reconcile_stale())

    async def get_fabula(self, actor: Actor, path: Path, query: None, body: None):
        return await self.fabulas.get(path["fabula_id"])

    async def get_fabula_journal(self, actor: Actor, path: Path, query: None, body: None):
        return await self.fabulas.journal(path["fabula_id"])

    async def control_fabula(self, actor: Actor, path: Path, query: None, body: ControlFabula):
        return await self.fabulas.control(actor, path["fabula_id"], body)

    async def get_control_outcome(self, actor: Actor, path: Path, query: None, body: None):
        return await self.fabulas.outcome(path["fabula_id"], path["request_id"])

    async def analyze_fabula_swap(self, actor: Actor, path: Path, query: None, body: AnalyzeSwap):
        return await self.fabulas.analyze_swap(path["fabula_id"], body)

    async def import_fabula(self, actor: Actor, path: Path, query: None, body: ImportFabula):
        return await self.fabulas.import_fabula(actor, path["fabula_id"], body.request_id)

    async def reconcile_fabula(self, actor: Actor, path: Path, query: None, body: None):
        return await self.fabulas.reconcile(path["fabula_id"])

    # --- migrations -----------------------------------------------------------------

    async def create_migration(self, actor: Actor, path: Path, query: None, body: CreateCampaign):
        return await self.migrations.create(actor, body)

    async def list_migrations(self, actor: Actor, path: Path, query: MigrationQuery, body: None):
        return await self.migrations.list(query.scenario, query.status)

    async def get_migration(self, actor: Actor, path: Path, query: None, body: None):
        return await self.migrations.get(path["campaign_id"])

    async def list_migration_items(self, actor: Actor, path: Path, query: ItemQuery, body: None):
        return await self.migrations.items(path["campaign_id"], query.verdict, query.state, query.cluster_id, query.page_size, query.page_token)

    async def list_migration_clusters(self, actor: Actor, path: Path, query: None, body: None):
        return await self.migrations.clusters(path["campaign_id"])

    async def decide_migration(self, actor: Actor, path: Path, query: None, body: Decide):
        return await self.migrations.decide(actor, path["campaign_id"], body)

    async def apply_migration(self, actor: Actor, path: Path, query: None, body: ApplyCampaign):
        return await self.migrations.apply(actor, path["campaign_id"], body)

    async def refresh_migration(self, actor: Actor, path: Path, query: None, body: CampaignCommand):
        return await self.migrations.refresh(actor, path["campaign_id"], body)

    async def pause_migration(self, actor: Actor, path: Path, query: None, body: CampaignCommand):
        return await self.migrations.pause(actor, path["campaign_id"], body)

    async def resume_migration(self, actor: Actor, path: Path, query: None, body: CampaignCommand):
        return await self.migrations.resume(actor, path["campaign_id"], body)

    async def cancel_migration(self, actor: Actor, path: Path, query: None, body: CampaignCommand):
        return await self.migrations.cancel(actor, path["campaign_id"], body)

    async def revert_migration(self, actor: Actor, path: Path, query: None, body: CampaignCommand):
        return await self.migrations.revert(actor, path["campaign_id"], body)

    # --- agentic resolution ---------------------------------------------------------

    async def list_resolution_tasks(self, actor: Actor, path: Path, query: TaskQuery, body: None):
        return await self.resolution.list(query.campaign_id, query.status, query.pool)

    async def get_resolution_task(self, actor: Actor, path: Path, query: None, body: None):
        return await self.resolution.get(path["task_id"])

    async def lease_resolution_tasks(self, actor: Actor, path: Path, query: None, body: LeaseTasks):
        return await self.resolution.lease(actor, body)

    async def release_resolution_task(self, actor: Actor, path: Path, query: None, body: ReviewProposal):
        return await self.resolution.release(actor, path["task_id"], body)

    async def dry_run_resolution(self, actor: Actor, path: Path, query: None, body: DryRunProposal):
        return await self.resolution.dry_run(actor, path["task_id"], body.proposal)

    async def submit_resolution(self, actor: Actor, path: Path, query: None, body: SubmitProposal):
        return await self.resolution.submit(actor, path["task_id"], body)

    async def approve_resolution(self, actor: Actor, path: Path, query: None, body: ReviewProposal):
        return await self.resolution.approve(actor, path["task_id"], body)

    async def reject_resolution(self, actor: Actor, path: Path, query: None, body: ReviewProposal):
        return await self.resolution.reject(actor, path["task_id"], body)

    # --- audit ----------------------------------------------------------------------

    async def list_audit(self, actor: Actor, path: Path, query: AuditQuery, body: None) -> Any:
        return AuditPage(items=tuple(await self.manager.deps.audit_log.list(query.resource, query.limit)))
