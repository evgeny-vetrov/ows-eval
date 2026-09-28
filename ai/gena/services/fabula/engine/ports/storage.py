"""Storage ports used by the host."""

from typing import Any, Protocol

from ai.gena.services.fabula.engine.model.base import Frozen
from ai.gena.services.fabula.engine.model.commands import Command
from ai.gena.services.fabula.engine.model.observations import Observation
from ai.gena.services.fabula.engine.model.scenario import ScenarioSnapshot


class VersionConflict(Exception):
    pass


class FabulaNotFound(Exception):
    pass


class FabulaExists(Exception):
    pass


class Outbox(Frozen):
    """Commands and observations of the transition that produced `version`."""

    version: int
    commands: tuple[Command, ...] = ()
    observations: tuple[Observation, ...] = ()


class StoredFabula(Frozen):
    fabula_id: str
    document: dict[str, Any]
    version: int
    outbox: Outbox | None = None


class FabulaStore(Protocol):
    """Persisted fabula state with optimistic locking.

    * `document` is the JSON form of the state after `externalize`.
    * `create` stores version 1 and raises `FabulaExists` for a known id.
    * `load` returns the document, its version and the unacknowledged outbox.
    * `save` writes the document and the outbox atomically if the stored version equals
      `expected_version`, otherwise raises `VersionConflict`; returns the new version.
    * `ack_outbox(fabula_id, version)` drops the outbox if it belongs to `version`.
    * `with_pending_outbox()` lists fabulas whose last outbox was not acknowledged.
    """

    async def create(self, fabula_id: str, document: dict[str, Any], outbox: Outbox) -> int: ...

    async def load(self, fabula_id: str) -> StoredFabula: ...

    async def save(self, fabula_id: str, document: dict[str, Any], expected_version: int, outbox: Outbox) -> int: ...

    async def ack_outbox(self, fabula_id: str, version: int) -> None: ...

    async def with_pending_outbox(self) -> list[str]: ...


class ScenarioRepository(Protocol):
    """Published scenario versions. `ref` is `namespace/name:version`."""

    async def get(self, ref: str) -> ScenarioSnapshot | None: ...


class BlobStore(Protocol):
    """Content-addressed values. `put` is idempotent: the ref is the content hash."""

    async def put(self, ref: str, data: bytes) -> None: ...

    async def get(self, ref: str) -> bytes: ...


class JournalSink(Protocol):
    """Receives observations. `write` is idempotent by `(fabula_id, version)`."""

    async def write(self, fabula_id: str, version: int, observations: tuple[Observation, ...]) -> None: ...
