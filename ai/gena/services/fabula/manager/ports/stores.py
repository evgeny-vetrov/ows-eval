"""Storage ports of the manager.

Common contract: values go in and out as the frozen models; writes that take an
`expected_revision` store `revision + 1` or raise `model.errors.RevisionConflict`;
creating an existing key raises `model.errors.AlreadyExists`.
"""

from __future__ import annotations

from typing import Protocol

from ai.gena.services.fabula.manager.model.audit import AuditRecord
from ai.gena.services.fabula.manager.model.campaigns import CampaignItem, MigrationCampaign
from ai.gena.services.fabula.manager.model.fabulas import ControlOutcome, CountRow, FabulaQuery, FabulaRecord, JournalEntry
from ai.gena.services.fabula.manager.model.resolution import AgenticResolutionTask, TaskStatus
from ai.gena.services.fabula.manager.model.scenarios import ScenarioInfo, ScenarioVersion
from ai.gena.services.fabula.manager.model.tags import TagChange, TagState


class ScenarioStore(Protocol):
    """Scenarios and their immutable versions. Versions are never deleted."""

    async def create_scenario(self, info: ScenarioInfo) -> None: ...

    async def get_scenario(self, scenario: str) -> ScenarioInfo | None: ...

    async def update_scenario(self, info: ScenarioInfo, expected_revision: int) -> None: ...

    async def list_scenarios(self, namespace: str | None = None) -> list[ScenarioInfo]: ...

    async def add_version(self, version: ScenarioVersion) -> None:
        """Raises `AlreadyExists` if the scenario already has this version string."""
        ...

    async def get_version(self, scenario: str, version: str) -> ScenarioVersion | None: ...

    async def list_versions(self, scenario: str) -> list[ScenarioVersion]:
        """In publication order."""
        ...

    async def replace_version(self, version: ScenarioVersion) -> None:
        """Stores a status change; the content of a version never changes."""
        ...


class TagStore(Protocol):
    async def get(self, scenario: str, tag: str) -> TagState | None: ...

    async def list(self, scenario: str) -> list[TagState]: ...

    async def save(self, state: TagState, change: TagChange, expected_revision: int | None) -> None:
        """Writes the state and appends `change` to the history atomically.
        `expected_revision=None` creates the tag."""
        ...

    async def history(self, scenario: str, tag: str) -> list[TagChange]: ...


class FabulaIndex(Protocol):
    """Read model of fabulas. `query` returns records ordered by `(order_by, fabula_id)`
    descending, starting strictly after `after` when it is given."""

    async def insert(self, record: FabulaRecord) -> None: ...

    async def get(self, fabula_id: str) -> FabulaRecord | None: ...

    async def replace(self, record: FabulaRecord, expected_revision: int) -> None: ...

    async def query(self, query: FabulaQuery, limit: int, after: tuple[str, str] | None = None) -> list[FabulaRecord]: ...

    async def counts(self, scenario: str | None = None) -> list[CountRow]: ...


class JournalReader(Protocol):
    """Reads what `JournalSink.write` stored, ordered by version."""

    async def read(self, fabula_id: str, after_version: int = 0, limit: int = 1000) -> list[JournalEntry]: ...


class ControlOutcomes(Protocol):
    """Durable outcomes of control requests. `record` keeps the first outcome of a key."""

    async def record(self, outcome: ControlOutcome) -> None: ...

    async def get(self, fabula_id: str, request_id: str) -> ControlOutcome | None: ...


class CampaignStore(Protocol):
    async def create(self, campaign: MigrationCampaign) -> None: ...

    async def get(self, campaign_id: str) -> MigrationCampaign | None: ...

    async def save(self, campaign: MigrationCampaign, expected_revision: int) -> None: ...

    async def list(self, scenario: str | None = None) -> list[MigrationCampaign]: ...

    async def put_item(self, item: CampaignItem, expected_revision: int | None) -> None:
        """`expected_revision=None` adds a new item."""
        ...

    async def get_item(self, campaign_id: str, fabula_id: str) -> CampaignItem | None: ...

    async def list_items(self, campaign_id: str) -> list[CampaignItem]:
        """In the order the items were added."""
        ...

    async def claim(self, fabula_id: str, campaign_id: str) -> str:
        """Makes `campaign_id` the fabula's active campaign unless another one holds it;
        returns the holder."""
        ...

    async def release(self, fabula_id: str, campaign_id: str) -> None: ...


class ResolutionTaskStore(Protocol):
    async def create(self, task: AgenticResolutionTask) -> None: ...

    async def get(self, task_id: str) -> AgenticResolutionTask | None: ...

    async def save(self, task: AgenticResolutionTask, expected_revision: int) -> None: ...

    async def list(
        self, campaign_id: str | None = None, status: TaskStatus | None = None, pool: str | None = None
    ) -> list[AgenticResolutionTask]:
        """In creation order."""
        ...


class AuditLog(Protocol):
    async def append(self, record: AuditRecord) -> None: ...

    async def list(self, resource: str | None = None, limit: int = 100) -> list[AuditRecord]:
        """Newest first; `resource` matches exactly or as a prefix ending with ':'."""
        ...
