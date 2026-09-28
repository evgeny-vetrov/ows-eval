"""Every API operation: HTTP method and path, models, rights and error codes.

The table is the single source of the OpenAPI document and of `dispatch`. Custom
methods follow the `resource:verb` convention (`/v1/migrations/{id}:apply`).
"""

import re
from dataclasses import dataclass
from typing import Any

from ai.gena.services.fabula.engine.model.swap import SwapReport
from ai.gena.services.fabula.manager.api.schemas import (
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
from ai.gena.services.fabula.manager.model.campaigns import (
    ApplyCampaign,
    ApplyResult,
    CampaignCommand,
    CampaignList,
    ClusterList,
    CreateCampaign,
    Decide,
    DecideResult,
    ItemPage,
    MigrationCampaign,
    RevertResult,
)
from ai.gena.services.fabula.manager.model.diff import ScenarioDiff
from ai.gena.services.fabula.manager.model.fabulas import (
    AnalyzeSwap,
    ControlAck,
    ControlFabula,
    ControlOutcome,
    Counts,
    FabulaDetails,
    FabulaPage,
    FabulaRecord,
    Journal,
    StartFabula,
    StartResult,
)
from ai.gena.services.fabula.manager.model.resolution import (
    AgenticResolutionTask,
    DryRunProposal,
    DryRunResult,
    LeasedTasks,
    LeaseTasks,
    ReviewProposal,
    SetResolutionPolicy,
    SubmitProposal,
    TaskList,
)
from ai.gena.services.fabula.manager.model.scenarios import (
    CreateScenario,
    DeprecateVersion,
    PublishVersion,
    ScenarioInfo,
    ScenarioList,
    ScenarioVersion,
    UpdateScenario,
    ValidateScenario,
    ValidationResult,
    VersionList,
)
from ai.gena.services.fabula.manager.model.tags import Assignment, MoveTag, RollbackTag, SetAllocation, TagHistory, TagList, TagMoveResult, TagState

_PARAM = re.compile(r"{(\w+)}")


@dataclass(frozen=True)
class Operation:
    handler: str
    method: str
    path: str
    # None: any authenticated actor may call it.
    permission: str | None
    tag: str
    summary: str
    response: Any
    body: Any = None
    query: Any = None
    status: int = 200
    errors: tuple[int, ...] = ()

    @property
    def operation_id(self) -> str:
        head, *rest = self.handler.split("_")
        return head + "".join(part.capitalize() for part in rest)

    @property
    def path_params(self) -> tuple[str, ...]:
        return tuple(_PARAM.findall(self.path))

    def resource(self, path: dict[str, str], body: Any) -> str:
        if "namespace" in path:
            return f"scenario:{path['namespace']}/{path['name']}"
        if "fabula_id" in path:
            return f"fabula:{path['fabula_id']}"
        if "campaign_id" in path:
            return f"migration:{path['campaign_id']}"
        if "task_id" in path:
            return f"resolution-task:{path['task_id']}"
        scenario = getattr(body, "scenario", None)
        return f"scenario:{scenario}" if isinstance(scenario, str) else "*"


SCENARIO = "/v1/scenarios/{namespace}/{name}"
FABULA = "/v1/fabulas/{fabula_id}"
MIGRATION = "/v1/migrations/{campaign_id}"
TASK = "/v1/agentic-resolutions/{task_id}"
MUTATION = (409, 412)

OPERATIONS: tuple[Operation, ...] = (
    # scenarios and versions
    Operation("list_scenarios", "GET", "/v1/scenarios", "scenario.read", "scenarios", "List scenarios", ScenarioList, query=NamespaceQuery),
    Operation("create_scenario", "POST", "/v1/scenarios", "scenario.write", "scenarios", "Create a scenario", ScenarioInfo, body=CreateScenario, status=201, errors=(409,)),
    Operation("validate_scenario", "POST", "/v1/scenarios:validate", "scenario.read", "scenarios", "Validate a document without publishing it", ValidationResult, body=ValidateScenario),
    Operation("get_scenario", "GET", SCENARIO, "scenario.read", "scenarios", "Get a scenario", ScenarioInfo, errors=(404,)),
    Operation("update_scenario", "PATCH", SCENARIO, "scenario.write", "scenarios", "Update scenario metadata", ScenarioInfo, body=UpdateScenario, errors=(404, *MUTATION)),
    Operation(
        "set_resolution_policy", "PUT", SCENARIO + "/resolution-policy", "scenario.write", "scenarios",
        "Set who may resolve migration conflicts and within which limits", ScenarioInfo, body=SetResolutionPolicy, errors=(404, *MUTATION),
    ),
    Operation("publish_version", "POST", SCENARIO + "/versions", "scenario.publish", "scenarios", "Publish a version", ScenarioVersion, body=PublishVersion, status=201, errors=(409, 422)),
    Operation("list_versions", "GET", SCENARIO + "/versions", "scenario.read", "scenarios", "List versions", VersionList, errors=(404,)),
    Operation("get_version", "GET", SCENARIO + "/versions/{version}", "scenario.read", "scenarios", "Get a version with its source", ScenarioVersion, errors=(404,)),
    Operation(
        "deprecate_version", "POST", SCENARIO + "/versions/{version}:deprecate", "scenario.publish", "scenarios",
        "Stop new starts on a version", ScenarioVersion, body=DeprecateVersion, errors=(404, 409),
    ),
    Operation("diff_versions", "GET", SCENARIO + "/diff", "scenario.read", "scenarios", "Diff two versions", ScenarioDiff, query=DiffQuery, errors=(404,)),
    Operation("list_capabilities", "GET", "/v1/capabilities", "scenario.read", "catalogs", "Browse capabilities", CapabilityList, query=CatalogQuery),
    Operation("list_event_types", "GET", "/v1/event-types", "scenario.read", "catalogs", "Browse event types", EventTypeList, query=CatalogQuery),
    # tags
    Operation("list_tags", "GET", SCENARIO + "/tags", "scenario.read", "tags", "List tags", TagList),
    Operation("get_tag", "GET", SCENARIO + "/tags/{tag}", "scenario.read", "tags", "Get a tag", TagState, errors=(404,)),
    Operation(
        "move_tag", "POST", SCENARIO + "/tags/{tag}:move", "tag.write", "tags",
        "Point a tag to a version; by default opens a migration campaign asking about running fabulas", TagMoveResult, body=MoveTag, errors=(404, *MUTATION),
    ),
    Operation("allocate_tag", "POST", SCENARIO + "/tags/{tag}:allocate", "tag.write", "tags", "Split new starts between versions (A/B)", TagMoveResult, body=SetAllocation, errors=(404, *MUTATION)),
    Operation(
        "rollback_tag", "POST", SCENARIO + "/tags/{tag}:rollback", "tag.write", "tags",
        "Point a tag back to an earlier revision or version", TagMoveResult, body=RollbackTag, errors=(404, *MUTATION),
    ),
    Operation("get_tag_history", "GET", SCENARIO + "/tags/{tag}/history", "scenario.read", "tags", "Tag history", TagHistory, errors=(404,)),
    Operation("resolve_tag", "GET", SCENARIO + "/tags/{tag}:resolve", "scenario.read", "tags", "Preview the version a routing key gets", Assignment, query=RoutingQuery, errors=(404,)),
    # fabulas
    Operation(
        "start_fabula", "POST", "/v1/fabulas", "fabula.start", "fabulas",
        "Start a fabula (201) or return the one this request started (200)", StartResult, body=StartFabula, status=201, errors=(404, 409),
    ),
    Operation("search_fabulas", "GET", "/v1/fabulas", "fabula.read", "fabulas", "Find fabulas", FabulaPage, query=FabulaSearch),
    Operation("count_fabulas", "GET", "/v1/fabulas:count", "fabula.read", "fabulas", "Count fabulas by version and status", Counts, query=ScenarioFilter),
    Operation("reconcile_fabulas", "POST", "/v1/fabulas:reconcile", "fabula.control", "fabulas", "Repair stale index records", ReconcileResult),
    Operation("get_fabula", "GET", FABULA, "fabula.read", "fabulas", "Get a fabula", FabulaDetails, errors=(404,)),
    Operation("get_fabula_journal", "GET", FABULA + "/journal", "fabula.read", "fabulas", "Step journal", Journal, errors=(404,)),
    Operation("control_fabula", "POST", FABULA + "/controls", "fabula.control", "fabulas", "Intervene", ControlAck, body=ControlFabula, status=202, errors=(404,)),
    Operation("get_control_outcome", "GET", FABULA + "/controls/{request_id}", "fabula.read", "fabulas", "Outcome of an intervention", ControlOutcome, errors=(404,)),
    Operation("analyze_fabula_swap", "POST", FABULA + ":analyze-swap", "fabula.read", "fabulas", "Preview a version swap", SwapReport, body=AnalyzeSwap, errors=(404,)),
    Operation("import_fabula", "POST", FABULA + ":import", "fabula.control", "fabulas", "Index a fabula started past the manager", FabulaRecord, body=ImportFabula, errors=(404,)),
    Operation("reconcile_fabula", "POST", FABULA + ":reconcile", "fabula.control", "fabulas", "Rebuild the index record", FabulaRecord, errors=(404,)),
    # migrations
    Operation(
        "create_migration", "POST", "/v1/migrations", "migration.write", "migrations",
        "Analyse running fabulas for a move to a version", MigrationCampaign, body=CreateCampaign, status=201, errors=(404, 409),
    ),
    Operation("list_migrations", "GET", "/v1/migrations", "migration.read", "migrations", "List migrations", CampaignList, query=MigrationQuery),
    Operation("get_migration", "GET", MIGRATION, "migration.read", "migrations", "Get a migration", MigrationCampaign, errors=(404,)),
    Operation("list_migration_items", "GET", MIGRATION + "/items", "migration.read", "migrations", "Fabulas of a migration", ItemPage, query=ItemQuery, errors=(404,)),
    Operation("list_migration_clusters", "GET", MIGRATION + "/clusters", "migration.read", "migrations", "Fabulas grouped by what blocks them", ClusterList, errors=(404,)),
    Operation(
        "decide_migration", "POST", MIGRATION + "/decisions", "migration.write", "migrations",
        "Decide: migrate, resolve, stay, delegate, cancel", DecideResult, body=Decide, errors=(404, *MUTATION),
    ),
    Operation("apply_migration", "POST", MIGRATION + ":apply", "migration.write", "migrations", "Move approved fabulas", ApplyResult, body=ApplyCampaign, errors=(404, 409)),
    Operation("refresh_migration", "POST", MIGRATION + ":refresh", "migration.write", "migrations", "Analyse again", MigrationCampaign, body=CampaignCommand, errors=(404, 409)),
    Operation("pause_migration", "POST", MIGRATION + ":pause", "migration.write", "migrations", "Pause applying", MigrationCampaign, body=CampaignCommand, errors=(404, 409)),
    Operation("resume_migration", "POST", MIGRATION + ":resume", "migration.write", "migrations", "Resume applying", MigrationCampaign, body=CampaignCommand, errors=(404,)),
    Operation(
        "cancel_migration", "POST", MIGRATION + ":cancel", "migration.write", "migrations",
        "Stop; unmoved fabulas stay on their version", MigrationCampaign, body=CampaignCommand, errors=(404,),
    ),
    Operation(
        "revert_migration", "POST", MIGRATION + ":revert", "migration.write", "migrations",
        "Open migrations moving migrated fabulas back", RevertResult, body=CampaignCommand, errors=(404, 409),
    ),
    # agentic resolution of migration conflicts
    Operation("list_resolution_tasks", "GET", "/v1/agentic-resolutions", "migration.read", "agentic-resolutions", "List resolution tasks", TaskList, query=TaskQuery),
    Operation("lease_resolution_tasks", "POST", "/v1/agentic-resolutions:lease", "resolution.work", "agentic-resolutions", "Lease tasks with their briefs", LeasedTasks, body=LeaseTasks),
    Operation("get_resolution_task", "GET", TASK, "migration.read", "agentic-resolutions", "Get a resolution task", AgenticResolutionTask, errors=(404,)),
    Operation("release_resolution_task", "POST", TASK + ":release", "resolution.work", "agentic-resolutions", "Give a leased task back", AgenticResolutionTask, body=ReviewProposal, errors=(404,)),
    Operation("dry_run_resolution", "POST", TASK + ":dry-run", "resolution.work", "agentic-resolutions", "Check a proposal without effects", DryRunResult, body=DryRunProposal, errors=(404,)),
    Operation(
        "submit_resolution", "POST", TASK + "/proposals", "resolution.work", "agentic-resolutions",
        "Submit a proposal; applied at once under agent_applies", AgenticResolutionTask, body=SubmitProposal, errors=(403, 404, 409, 422),
    ),
    Operation(
        "approve_resolution", "POST", TASK + ":approve", "resolution.approve", "agentic-resolutions",
        "Approve a proposal and apply it", AgenticResolutionTask, body=ReviewProposal, errors=(404, 409),
    ),
    Operation("reject_resolution", "POST", TASK + ":reject", "resolution.approve", "agentic-resolutions", "Reject a proposal", AgenticResolutionTask, body=ReviewProposal, errors=(404, 409)),
    # audit
    Operation("list_audit", "GET", "/v1/audit", "audit.read", "audit", "Who did what", AuditPage, query=AuditQuery),
)

BY_ID = {op.operation_id: op for op in OPERATIONS}
