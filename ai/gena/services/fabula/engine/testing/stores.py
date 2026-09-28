"""In-memory storage ports. Values are copied through JSON as a database would do."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from ai.gena.services.fabula.engine.model.observations import Observation
from ai.gena.services.fabula.engine.model.scenario import ScenarioSnapshot
from ai.gena.services.fabula.engine.ports.storage import FabulaExists, FabulaNotFound, Outbox, StoredFabula, VersionConflict


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


class FakeClock:
    def __init__(self, now: datetime = datetime(2026, 1, 1, tzinfo=timezone.utc)):
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> datetime:
        self._now += delta
        return self._now


class InMemoryFabulaStore:
    def __init__(self) -> None:
        self._rows: dict[str, dict[str, Any]] = {}
        self.saves = 0

    async def create(self, fabula_id: str, document: dict[str, Any], outbox: Outbox) -> int:
        await asyncio.sleep(0)
        if fabula_id in self._rows:
            raise FabulaExists(fabula_id)
        self._rows[fabula_id] = {"document": _copy(document), "version": 1, "outbox": outbox.model_dump_json()}
        return 1

    async def load(self, fabula_id: str) -> StoredFabula:
        await asyncio.sleep(0)
        row = self._rows.get(fabula_id)
        if row is None:
            raise FabulaNotFound(fabula_id)
        outbox = Outbox.model_validate_json(row["outbox"]) if row["outbox"] else None
        return StoredFabula(fabula_id=fabula_id, document=_copy(row["document"]), version=row["version"], outbox=outbox)

    async def save(self, fabula_id: str, document: dict[str, Any], expected_version: int, outbox: Outbox) -> int:
        await asyncio.sleep(0)
        row = self._rows.get(fabula_id)
        if row is None:
            raise FabulaNotFound(fabula_id)
        if row["version"] != expected_version:
            raise VersionConflict(f"{fabula_id}: expected version {expected_version}, stored {row['version']}")
        row.update(document=_copy(document), version=expected_version + 1, outbox=outbox.model_dump_json())
        self.saves += 1
        return expected_version + 1

    async def ack_outbox(self, fabula_id: str, version: int) -> None:
        await asyncio.sleep(0)
        row = self._rows[fabula_id]
        if row["outbox"] and Outbox.model_validate_json(row["outbox"]).version == version:
            row["outbox"] = None

    async def with_pending_outbox(self) -> list[str]:
        return [fabula_id for fabula_id, row in self._rows.items() if row["outbox"]]

    def document(self, fabula_id: str) -> dict[str, Any]:
        return self._rows[fabula_id]["document"]


class InMemoryScenarioRepository:
    def __init__(self) -> None:
        self._versions: dict[str, ScenarioSnapshot] = {}

    def publish(self, snapshot: ScenarioSnapshot) -> str:
        self._versions[snapshot.ref] = snapshot
        return snapshot.ref

    async def get(self, ref: str) -> ScenarioSnapshot | None:
        return self._versions.get(ref)


class InMemoryBlobStore:
    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    async def put(self, ref: str, data: bytes) -> None:
        self._blobs.setdefault(ref, data)

    async def get(self, ref: str) -> bytes:
        return self._blobs[ref]

    def __len__(self) -> int:
        return len(self._blobs)


class InMemoryJournal:
    """`JournalSink` keyed by `(fabula_id, version)`: rewriting a version is a no-op."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, int], tuple[Observation, ...]] = {}
        self.writes = 0

    async def write(self, fabula_id: str, version: int, observations: tuple[Observation, ...]) -> None:
        self.writes += 1
        self._entries.setdefault((fabula_id, version), tuple(observations))

    def observations(self, fabula_id: str) -> list[Observation]:
        versions = sorted(v for f, v in self._entries if f == fabula_id)
        return [o for v in versions for o in self._entries[(fabula_id, v)]]
