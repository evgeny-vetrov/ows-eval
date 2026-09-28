"""The fabula index as a projection of the journal.

`ManagerJournalSink` wraps the host's `JournalSink`: after the journal write it records
control outcomes and applies the observations to the index. Journal versions are
applied strictly in order, because `StatusChanged` and `Waiting` are emitted only when
something changes. A version that arrives out of order marks the record stale; so does
a failure of the index, which must never fail the host's flush (the fabula would stall
on its unacknowledged outbox). `IndexProjector.reconcile` replays the journal and
overlays the authoritative view.
"""

import logging
from collections.abc import Iterable

from ai.gena.services.fabula.engine.core.projection import FabulaView
from ai.gena.services.fabula.engine.model.observations import Observation
from ai.gena.services.fabula.engine.ports.storage import JournalSink
from ai.gena.services.fabula.manager.model.common import split_ref
from ai.gena.services.fabula.manager.model.errors import AlreadyExists, RevisionConflict
from ai.gena.services.fabula.manager.model.fabulas import ControlOutcome, FabulaRecord, SwapRecord
from ai.gena.services.fabula.manager.ports.stores import ControlOutcomes, FabulaIndex, JournalReader

log = logging.getLogger(__name__)

_CAS_ATTEMPTS = 8
# Observations that do not change where the fabula is.
_PASSIVE = {"stimulus_ignored", "stimulus_queued", "control_rejected"}


def project(record: FabulaRecord, version: int, observations: Iterable[Observation]) -> FabulaRecord:
    """Apply one journal version to a record. Every change sets a value, so replaying a
    version over a record that already reflects it changes nothing but `swaps`."""
    changes: dict = {"journal_version": version}
    swaps = list(record.swaps)
    at_rest = record.at_rest
    active = False
    for observation in observations:
        kind = observation.kind
        changes["updated_at"] = observation.at
        if kind not in _PASSIVE:
            active = True
        if kind == "fabula_started":
            scenario, number = split_ref(observation.scenario_ref)
            changes.update(scenario=scenario, ref=observation.scenario_ref, version=number, status="running", started_at=observation.at)
        elif kind == "status_changed":
            changes["status"] = observation.status
            if observation.status != "failed":
                changes["failed_node"] = None
        elif kind == "waiting":
            changes["waits"] = observation.waits
        elif kind == "safe_point":
            at_rest = True
        elif kind == "scenario_swapped":
            scenario, number = split_ref(observation.ref)
            changes.update(ref=observation.ref, version=number)
            swaps.append(SwapRecord(at=observation.at, from_ref=observation.previous_ref, to_ref=observation.ref))
        elif kind == "fabula_completed":
            changes.update(status="completed", waits=())
        elif kind == "fabula_cancelled":
            changes.update(status="cancelled", waits=())
        elif kind == "fabula_failed":
            changes.update(status="failed", waits=(), last_error=observation.problem, failed_node=observation.node)
    if active and not any(o.kind == "safe_point" for o in observations):
        at_rest = False
    changes.update(swaps=tuple(swaps), at_rest=at_rest)
    return record.model_copy(update=changes)


def external_record(fabula_id: str, observations: tuple[Observation, ...]) -> FabulaRecord | None:
    """A record for a fabula started past the manager, from its first journal version."""
    started = next((o for o in observations if o.kind == "fabula_started"), None)
    if started is None:
        return None
    scenario, version = split_ref(started.scenario_ref)
    return FabulaRecord(
        fabula_id=fabula_id,
        scenario=scenario,
        ref=started.scenario_ref,
        version=version,
        status="running",
        registered_at=started.at,
        updated_at=started.at,
        origin="external",
    )


def overlay_view(record: FabulaRecord, view: FabulaView) -> FabulaRecord:
    scenario, version = split_ref(view.scenario_ref)
    return record.model_copy(
        update=dict(
            scenario=scenario,
            ref=view.scenario_ref,
            version=version,
            status=view.status,
            waits=view.waits,
            last_error=view.last_error,
            failed_node=view.failed_node,
            started_at=view.started_at,
            updated_at=max(record.updated_at, view.updated_at),
        )
    )


def outcomes_of(fabula_id: str, version: int, observations: tuple[Observation, ...]) -> list[ControlOutcome]:
    swapped = next((o for o in observations if o.kind == "scenario_swapped"), None)
    found = []
    for o in observations:
        if o.kind == "control_accepted":
            swap = swapped if o.action == "swap_version" else None
            found.append(
                ControlOutcome(
                    fabula_id=fabula_id,
                    request_id=o.request_id,
                    action=o.action,
                    accepted=True,
                    actor=o.actor,
                    at=o.at,
                    journal_version=version,
                    report=swap.report if swap else None,
                    swapped_to=swap.ref if swap else None,
                )
            )
        elif o.kind == "control_rejected":
            found.append(
                ControlOutcome(
                    fabula_id=fabula_id,
                    request_id=o.request_id,
                    action=o.action,
                    accepted=False,
                    code=o.code,
                    message=o.message,
                    actor=o.actor,
                    at=o.at,
                    journal_version=version,
                    report=o.report,
                )
            )
    return found


class IndexProjector:
    def __init__(self, index: FabulaIndex, journal: JournalReader):
        self.index = index
        self.journal = journal

    async def apply(self, fabula_id: str, version: int, observations: tuple[Observation, ...]) -> None:
        for _ in range(_CAS_ATTEMPTS):
            record = await self.index.get(fabula_id)
            try:
                if record is None:
                    fresh = external_record(fabula_id, observations) if version == 1 else None
                    if fresh is not None:
                        await self.index.insert(project(fresh, version, observations))
                    return
                if version <= record.journal_version:
                    return
                if version != record.journal_version + 1:
                    if not record.stale:
                        await self.index.replace(record.model_copy(update={"stale": True}), record.revision)
                    return
                await self.index.replace(project(record, version, observations), record.revision)
                return
            except (RevisionConflict, AlreadyExists):
                continue
        raise RevisionConflict(f"index record of {fabula_id} keeps changing")

    async def mark_stale(self, fabula_id: str) -> None:
        record = await self.index.get(fabula_id)
        if record is not None and not record.stale:
            await self.index.replace(record.model_copy(update={"stale": True}), record.revision)

    async def replay(self, record: FabulaRecord) -> FabulaRecord:
        """Apply the journal versions after the record's, while they are contiguous."""
        gap = False
        while True:
            entries = await self.journal.read(record.fabula_id, after_version=record.journal_version)
            if not entries:
                break
            for entry in entries:
                if entry.version != record.journal_version + 1:
                    gap = True
                    break
                record = project(record, entry.version, entry.observations)
            if gap:
                break
        return record.model_copy(update={"stale": gap})


class ManagerJournalSink:
    """`JournalSink` for the host: the journal first, then outcomes and the index."""

    def __init__(self, inner: JournalSink, projector: IndexProjector, outcomes: ControlOutcomes):
        self.inner = inner
        self.projector = projector
        self.outcomes = outcomes

    async def write(self, fabula_id: str, version: int, observations: tuple[Observation, ...]) -> None:
        await self.inner.write(fabula_id, version, observations)
        try:
            for outcome in outcomes_of(fabula_id, version, observations):
                await self.outcomes.record(outcome)
        except Exception:
            log.exception("control outcomes of %s@%s were not recorded; they are recovered from the journal", fabula_id, version)
        try:
            await self.projector.apply(fabula_id, version, observations)
        except Exception:
            log.exception("index update of %s@%s failed", fabula_id, version)
            try:
                await self.projector.mark_stale(fabula_id)
            except Exception:
                log.exception("could not mark %s stale; the next version will", fabula_id)
