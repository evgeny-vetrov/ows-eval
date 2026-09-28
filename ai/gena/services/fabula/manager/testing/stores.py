"""In-memory implementations of the manager's storage ports."""

from __future__ import annotations

import asyncio
from collections import Counter, defaultdict

from ai.gena.services.fabula.engine.model.observations import Observation
from ai.gena.services.fabula.manager.model.audit import AuditRecord
from ai.gena.services.fabula.manager.model.campaigns import CampaignItem, MigrationCampaign
from ai.gena.services.fabula.manager.model.errors import AlreadyExists, NotFound, RevisionConflict
from ai.gena.services.fabula.manager.model.fabulas import ControlOutcome, CountRow, FabulaQuery, FabulaRecord, JournalEntry
from ai.gena.services.fabula.manager.model.resolution import AgenticResolutionTask, TaskStatus
from ai.gena.services.fabula.manager.model.scenarios import ScenarioInfo, ScenarioVersion
from ai.gena.services.fabula.manager.model.tags import TagChange, TagState


def _check(current: int | None, expected: int | None, what: str) -> None:
    if expected is None:
        if current is not None:
            raise AlreadyExists(f"{what} already exists")
    elif current is None:
        raise NotFound(f"{what} does not exist")
    elif current != expected:
        raise RevisionConflict(f"{what}: expected revision {expected}, stored {current}")


class InMemoryScenarioStore:
    def __init__(self) -> None:
        self._scenarios: dict[str, ScenarioInfo] = {}
        self._versions: dict[str, dict[str, ScenarioVersion]] = defaultdict(dict)

    async def create_scenario(self, info: ScenarioInfo) -> None:
        await asyncio.sleep(0)
        _check(self._scenarios[info.scenario].revision if info.scenario in self._scenarios else None, None, f"scenario {info.scenario}")
        self._scenarios[info.scenario] = info

    async def get_scenario(self, scenario: str) -> ScenarioInfo | None:
        return self._scenarios.get(scenario)

    async def update_scenario(self, info: ScenarioInfo, expected_revision: int) -> None:
        await asyncio.sleep(0)
        current = self._scenarios.get(info.scenario)
        _check(current.revision if current else None, expected_revision, f"scenario {info.scenario}")
        self._scenarios[info.scenario] = info.model_copy(update={"revision": expected_revision + 1})

    async def list_scenarios(self, namespace: str | None = None) -> list[ScenarioInfo]:
        return [info for key, info in sorted(self._scenarios.items()) if namespace is None or key.startswith(namespace + "/")]

    async def add_version(self, version: ScenarioVersion) -> None:
        await asyncio.sleep(0)
        if version.version in self._versions[version.scenario]:
            raise AlreadyExists(f"{version.ref} already exists")
        self._versions[version.scenario][version.version] = version

    async def get_version(self, scenario: str, version: str) -> ScenarioVersion | None:
        return self._versions.get(scenario, {}).get(version)

    async def list_versions(self, scenario: str) -> list[ScenarioVersion]:
        return list(self._versions.get(scenario, {}).values())

    async def replace_version(self, version: ScenarioVersion) -> None:
        if version.version not in self._versions.get(version.scenario, {}):
            raise NotFound(f"{version.ref} does not exist")
        self._versions[version.scenario][version.version] = version


class InMemoryTagStore:
    def __init__(self) -> None:
        self._tags: dict[tuple[str, str], TagState] = {}
        self._history: dict[tuple[str, str], list[TagChange]] = defaultdict(list)

    async def get(self, scenario: str, tag: str) -> TagState | None:
        return self._tags.get((scenario, tag))

    async def list(self, scenario: str) -> list[TagState]:
        return [state for (s, _), state in sorted(self._tags.items()) if s == scenario]

    async def save(self, state: TagState, change: TagChange, expected_revision: int | None) -> None:
        await asyncio.sleep(0)
        key = (state.scenario, state.tag)
        current = self._tags.get(key)
        _check(current.revision if current else None, expected_revision, f"tag {state.scenario}@{state.tag}")
        self._tags[key] = state
        self._history[key].append(change)

    async def history(self, scenario: str, tag: str) -> list[TagChange]:
        return list(self._history.get((scenario, tag), []))


def _sort_key(record: FabulaRecord, order_by: str) -> tuple[str, str]:
    moment = record.updated_at if order_by == "updated_at" else (record.started_at or record.registered_at)
    return (moment.isoformat(), record.fabula_id)


def _matches(record: FabulaRecord, query: FabulaQuery) -> bool:
    assignment = record.assignment
    started = record.started_at or record.registered_at
    return (
        (query.scenario is None or record.scenario == query.scenario)
        and (not query.versions or record.version in query.versions)
        and (not query.statuses or record.status in query.statuses)
        and (query.tag is None or (assignment is not None and assignment.tag == query.tag))
        and (query.arm is None or (assignment is not None and assignment.arm == query.arm))
        and all(record.labels.get(key) == value for key, value in query.labels.items())
        and (query.waiting_on is None or any(wait.node == query.waiting_on for wait in record.waits))
        and (query.started_after is None or started >= query.started_after)
        and (query.started_before is None or started < query.started_before)
        and (query.stale is None or record.stale == query.stale)
        and (not query.fabula_ids or record.fabula_id in query.fabula_ids)
    )


class InMemoryFabulaIndex:
    def __init__(self) -> None:
        self._records: dict[str, FabulaRecord] = {}
        self.fail_next: int = 0  # fault injection: fail this many writes

    def _maybe_fail(self) -> None:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise ConnectionError("index is unavailable")

    async def insert(self, record: FabulaRecord) -> None:
        await asyncio.sleep(0)
        self._maybe_fail()
        if record.fabula_id in self._records:
            raise AlreadyExists(f"fabula {record.fabula_id} is already indexed")
        self._records[record.fabula_id] = record

    async def get(self, fabula_id: str) -> FabulaRecord | None:
        return self._records.get(fabula_id)

    async def replace(self, record: FabulaRecord, expected_revision: int) -> None:
        await asyncio.sleep(0)
        self._maybe_fail()
        current = self._records.get(record.fabula_id)
        _check(current.revision if current else None, expected_revision, f"fabula {record.fabula_id}")
        self._records[record.fabula_id] = record.model_copy(update={"revision": expected_revision + 1})

    async def query(self, query: FabulaQuery, limit: int, after: tuple[str, str] | None = None) -> list[FabulaRecord]:
        found = sorted((r for r in self._records.values() if _matches(r, query)), key=lambda r: _sort_key(r, query.order_by), reverse=True)
        if after is not None:
            found = [r for r in found if _sort_key(r, query.order_by) < tuple(after)]
        return found[:limit]

    async def counts(self, scenario: str | None = None) -> list[CountRow]:
        counter = Counter((r.scenario, r.version, r.status) for r in self._records.values() if scenario is None or r.scenario == scenario)
        return [CountRow(scenario=s, version=v, status=st, count=n) for (s, v, st), n in sorted(counter.items())]

    def all(self) -> list[FabulaRecord]:
        return list(self._records.values())


class InMemoryJournalStore:
    """`JournalSink` and `JournalReader` over one dictionary; writing a version twice keeps the first."""

    def __init__(self) -> None:
        self._entries: dict[str, dict[int, tuple[Observation, ...]]] = defaultdict(dict)

    async def write(self, fabula_id: str, version: int, observations: tuple[Observation, ...]) -> None:
        await asyncio.sleep(0)
        self._entries[fabula_id].setdefault(version, tuple(observations))

    async def read(self, fabula_id: str, after_version: int = 0, limit: int = 1000) -> list[JournalEntry]:
        versions = sorted(v for v in self._entries.get(fabula_id, {}) if v > after_version)[:limit]
        return [JournalEntry(version=v, observations=self._entries[fabula_id][v]) for v in versions]

    def observations(self, fabula_id: str) -> list[Observation]:
        entries = self._entries.get(fabula_id, {})
        return [o for v in sorted(entries) for o in entries[v]]


class InMemoryControlOutcomes:
    def __init__(self) -> None:
        self._outcomes: dict[tuple[str, str], ControlOutcome] = {}

    async def record(self, outcome: ControlOutcome) -> None:
        self._outcomes.setdefault((outcome.fabula_id, outcome.request_id), outcome)

    async def get(self, fabula_id: str, request_id: str) -> ControlOutcome | None:
        return self._outcomes.get((fabula_id, request_id))


class InMemoryCampaignStore:
    def __init__(self) -> None:
        self._campaigns: dict[str, MigrationCampaign] = {}
        self._items: dict[str, dict[str, CampaignItem]] = defaultdict(dict)
        self._claims: dict[str, str] = {}

    async def create(self, campaign: MigrationCampaign) -> None:
        await asyncio.sleep(0)
        if campaign.campaign_id in self._campaigns:
            raise AlreadyExists(f"campaign {campaign.campaign_id} already exists")
        self._campaigns[campaign.campaign_id] = campaign

    async def get(self, campaign_id: str) -> MigrationCampaign | None:
        return self._campaigns.get(campaign_id)

    async def save(self, campaign: MigrationCampaign, expected_revision: int) -> None:
        await asyncio.sleep(0)
        current = self._campaigns.get(campaign.campaign_id)
        _check(current.revision if current else None, expected_revision, f"campaign {campaign.campaign_id}")
        self._campaigns[campaign.campaign_id] = campaign.model_copy(update={"revision": expected_revision + 1})

    async def list(self, scenario: str | None = None) -> list[MigrationCampaign]:
        return [c for c in self._campaigns.values() if scenario is None or c.scenario == scenario]

    async def put_item(self, item: CampaignItem, expected_revision: int | None) -> None:
        await asyncio.sleep(0)
        items = self._items[item.campaign_id]
        current = items.get(item.fabula_id)
        _check(current.revision if current else None, expected_revision, f"item {item.campaign_id}/{item.fabula_id}")
        items[item.fabula_id] = item if expected_revision is None else item.model_copy(update={"revision": expected_revision + 1})

    async def get_item(self, campaign_id: str, fabula_id: str) -> CampaignItem | None:
        return self._items.get(campaign_id, {}).get(fabula_id)

    async def list_items(self, campaign_id: str) -> list[CampaignItem]:
        return list(self._items.get(campaign_id, {}).values())

    async def claim(self, fabula_id: str, campaign_id: str) -> str:
        return self._claims.setdefault(fabula_id, campaign_id)

    async def release(self, fabula_id: str, campaign_id: str) -> None:
        if self._claims.get(fabula_id) == campaign_id:
            del self._claims[fabula_id]


class InMemoryResolutionTaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, AgenticResolutionTask] = {}

    async def create(self, task: AgenticResolutionTask) -> None:
        if task.task_id in self._tasks:
            raise AlreadyExists(f"task {task.task_id} already exists")
        self._tasks[task.task_id] = task

    async def get(self, task_id: str) -> AgenticResolutionTask | None:
        return self._tasks.get(task_id)

    async def save(self, task: AgenticResolutionTask, expected_revision: int) -> None:
        await asyncio.sleep(0)
        current = self._tasks.get(task.task_id)
        _check(current.revision if current else None, expected_revision, f"task {task.task_id}")
        self._tasks[task.task_id] = task.model_copy(update={"revision": expected_revision + 1})

    async def list(self, campaign_id: str | None = None, status: TaskStatus | None = None, pool: str | None = None) -> list[AgenticResolutionTask]:
        return [
            t
            for t in self._tasks.values()
            if (campaign_id is None or t.campaign_id == campaign_id) and (status is None or t.status == status) and (pool is None or t.pool == pool)
        ]


class InMemoryAuditLog:
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    async def append(self, record: AuditRecord) -> None:
        self.records.append(record)

    async def list(self, resource: str | None = None, limit: int = 100) -> list[AuditRecord]:
        def matches(record: AuditRecord) -> bool:
            return resource is None or record.resource == resource or (resource.endswith(":") and record.resource.startswith(resource))

        return [r for r in reversed(self.records) if matches(r)][:limit]

    def actions(self, resource: str | None = None) -> list[str]:
        return [r.action for r in self.records if resource is None or r.resource == resource]
