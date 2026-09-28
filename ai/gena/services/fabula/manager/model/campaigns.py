"""Migration campaigns: moving running fabulas of a scenario to another version.

Nothing moves without a decision: the campaign analyses every candidate, groups the
results into clusters and waits. Undecided fabulas stay on their version.
"""

from typing import Literal

from pydantic import Field

from ai.gena.services.fabula.engine.model.base import Frozen, UtcDatetime
from ai.gena.services.fabula.engine.model.swap import CursorMove, SwapReport
from ai.gena.services.fabula.manager.model.common import RequestBase
from ai.gena.services.fabula.manager.model.fabulas import RecordStatus
from ai.gena.services.fabula.manager.model.resolution import ResolutionPolicy

Verdict = Literal["in_flight", "with_restarts", "blocked", "busy", "not_eligible"]
ItemState = Literal["pending", "approved", "applying", "staying", "delegated", "migrated", "diverged", "not_eligible", "cancelled"]
FINAL_ITEM_STATES: tuple[ItemState, ...] = ("staying", "migrated", "diverged", "not_eligible", "cancelled")
Fence = Literal["none", "pausing", "paused"]
# awaiting_decision: some items need a decision; ready: approved items wait for `apply`.
CampaignStatus = Literal["awaiting_decision", "ready", "paused", "done", "cancelled"]
CampaignOrigin = Literal["manual", "tag_move", "revert"]
DecisionAction = Literal["migrate", "resolve", "stay", "delegate_to_agent", "cancel_fabula"]


class CampaignSource(Frozen):
    """Which fabulas the campaign considers. Empty `versions` means every version but
    the target; `statuses` defaults to every status that can still move."""

    versions: tuple[str, ...] = ()
    tag: str | None = None
    statuses: tuple[RecordStatus, ...] = ("running", "waiting", "paused", "failed")
    fabula_ids: tuple[str, ...] = ()


class Decision(Frozen):
    action: DecisionAction
    actor: str
    at: UtcDatetime
    request_id: str
    reason: str = ""
    task_id: str | None = None


class CampaignItem(Frozen):
    campaign_id: str
    fabula_id: str
    from_ref: str
    verdict: Verdict
    state: ItemState
    report: SwapReport | None = None
    cluster_id: str = ""
    # Per-item overrides from `resolve` decisions; otherwise the campaign's mapping.
    mapping: dict[str, str] | None = None
    context_migration: str | None = None
    approved_report: SwapReport | None = None
    decision: Decision | None = None
    attempt: int = 1
    fence: Fence = "none"
    task_id: str | None = None
    note: str = ""
    updated_at: UtcDatetime
    revision: int = 1


class MigrationCampaign(Frozen):
    campaign_id: str
    scenario: str
    target_version: str
    target_ref: str
    source: CampaignSource
    # Applied to every source version, over the diff's suggestions.
    mapping: dict[str, str] = Field(default_factory=dict)
    use_suggested_mapping: bool = True
    suggested_mappings: dict[str, dict[str, str]] = Field(default_factory=dict)
    context_migration: str | None = None
    policy: ResolutionPolicy
    auto_apply_compatible: bool = False
    batch_size: int = 100
    status: CampaignStatus = "awaiting_decision"
    origin: CampaignOrigin = "manual"
    reverts: str | None = None
    reason: str = ""
    request_id: str
    request_fingerprint: str
    created_by: str
    created_at: UtcDatetime
    updated_at: UtcDatetime
    counts: dict[str, int] = Field(default_factory=dict)
    # Set once the candidates were analysed; a replayed creation finishes the analysis.
    analyzed_at: UtcDatetime | None = None
    seen_requests: tuple[str, ...] = ()
    revision: int = 1

    def mapping_for(self, from_ref: str) -> dict[str, str]:
        suggested = self.suggested_mappings.get(from_ref, {}) if self.use_suggested_mapping else {}
        return {**suggested, **self.mapping}


class Cluster(Frozen):
    """Items of one source version whose analysis looks the same."""

    cluster_id: str
    from_ref: str
    verdict: Verdict
    cursor: tuple[CursorMove, ...] = ()
    problems: tuple[str, ...] = ()
    missing_variables: tuple[str, ...] = ()
    count: int
    states: dict[str, int] = Field(default_factory=dict)
    sample: tuple[str, ...] = ()
    mapping: dict[str, str] = Field(default_factory=dict)


# --- requests ------------------------------------------------------------------------


class CreateCampaign(RequestBase):
    scenario: str
    target_version: str
    source: CampaignSource = CampaignSource()
    mapping: dict[str, str] = Field(default_factory=dict)
    use_suggested_mapping: bool = True
    context_migration: str | None = None
    # None takes the scenario's policy.
    policy: ResolutionPolicy | None = None
    auto_apply_compatible: bool = False
    batch_size: int = Field(default=100, ge=1, le=10_000)
    reason: str = ""


class ItemTarget(Frozen):
    """Items a decision applies to: the union of the given clusters, ids and verdicts."""

    cluster_ids: tuple[str, ...] = ()
    fabula_ids: tuple[str, ...] = ()
    verdicts: tuple[Verdict, ...] = ()


class DecisionSpec(Frozen):
    target: ItemTarget
    action: DecisionAction
    mapping: dict[str, str] | None = None
    context_migration: str | None = None
    # For `resolve`: approve the items whose new analysis allows the swap.
    approve: bool = False
    pool: str = "default"
    reason: str = ""


class Decide(RequestBase):
    decisions: tuple[DecisionSpec, ...]
    expected_revision: int | None = None


class Skipped(Frozen):
    fabula_id: str
    reason: str


class DecideResult(Frozen):
    campaign_id: str
    replayed: bool = False
    affected: dict[str, int] = Field(default_factory=dict)
    skipped: tuple[Skipped, ...] = ()
    task_ids: tuple[str, ...] = ()


class ApplyCampaign(RequestBase):
    max_items: int | None = Field(default=None, ge=1)
    fabula_ids: tuple[str, ...] = ()


class ApplyResult(Frozen):
    campaign: MigrationCampaign
    processed: dict[str, int] = Field(default_factory=dict)


class CampaignCommand(RequestBase):
    reason: str = ""


class RevertResult(Frozen):
    campaign_ids: tuple[str, ...]


class CampaignList(Frozen):
    items: tuple[MigrationCampaign, ...]


class ItemPage(Frozen):
    items: tuple[CampaignItem, ...]
    next_page_token: str | None = None


class ClusterList(Frozen):
    items: tuple[Cluster, ...]
