"""Starting fabulas, finding them, reading their journal and intervening."""

import base64
import hashlib
import json
from datetime import timedelta

from ai.gena.services.fabula.engine.core.journal import build_journal
from ai.gena.services.fabula.engine.model.context import ExecutionContext
from ai.gena.services.fabula.engine.model.jsonvalue import digest
from ai.gena.services.fabula.engine.model.swap import SwapReport
from ai.gena.services.fabula.engine.ports.storage import FabulaNotFound
from ai.gena.services.fabula.manager.deps import Deps
from ai.gena.services.fabula.manager.fabulas.indexing import IndexProjector, outcomes_of, overlay_view
from ai.gena.services.fabula.manager.model.common import Actor, split_ref, version_ref
from ai.gena.services.fabula.manager.model.errors import AlreadyExists, Conflict, InvalidInput, NotFound, RevisionConflict
from ai.gena.services.fabula.manager.model.fabulas import (
    RESERVED_LABEL_PREFIX,
    AnalyzeSwap,
    ControlAck,
    ControlFabula,
    ControlOutcome,
    Counts,
    FabulaDetails,
    FabulaPage,
    FabulaQuery,
    FabulaRecord,
    Journal,
    StartFabula,
    StartResult,
    reserved_labels,
)
from ai.gena.services.fabula.manager.model.tags import Assignment
from ai.gena.services.fabula.manager.registry.tags import TagService

START_GRACE = timedelta(minutes=5)
_CAS_ATTEMPTS = 8


def derive_fabula_id(scenario: str, request_id: str) -> str:
    return "f-" + hashlib.sha256(f"{scenario}|{request_id}".encode("utf-8")).hexdigest()[:24]


def encode_token(key: tuple[str, str]) -> str:
    return base64.urlsafe_b64encode(json.dumps(list(key)).encode("utf-8")).decode("ascii")


def decode_token(token: str) -> tuple[str, str]:
    try:
        key = json.loads(base64.urlsafe_b64decode(token.encode("ascii")))
        if isinstance(key, list) and len(key) == 2 and all(isinstance(part, str) for part in key):
            return key[0], key[1]
    except ValueError:
        pass
    raise InvalidInput("page_token is malformed", code="bad_page_token")


def assignment_from_labels(labels: dict[str, str], ref: str) -> Assignment:
    scenario, version = split_ref(ref)
    tag = labels.get("fabula.tag")
    if tag is None:
        return Assignment(version=labels.get("fabula.version", version), ref=version_ref(scenario, labels.get("fabula.version", version)))
    return Assignment(
        version=labels.get("fabula.version", version),
        ref=version_ref(scenario, labels.get("fabula.version", version)),
        tag=tag,
        tag_revision=int(labels["fabula.tag_revision"]) if labels.get("fabula.tag_revision", "").isdigit() else None,
        arm=labels.get("fabula.arm"),
        bucket=int(labels["fabula.bucket"]) if labels.get("fabula.bucket", "").isdigit() else None,
    )


class FabulaService:
    def __init__(self, deps: Deps, tags: TagService, projector: IndexProjector):
        self.deps = deps
        self.tags = tags
        self.projector = projector

    # --- start ----------------------------------------------------------------------

    async def start(self, actor: Actor, request: StartFabula) -> StartResult:
        fabula_id = request.fabula_id or derive_fabula_id(request.scenario, request.request_id)
        fingerprint = digest(request.model_dump(mode="json", exclude={"fabula_id"}))
        existing = await self.deps.index.get(fabula_id)
        created = False
        if existing is None:
            assignment, _ = await self.tags.route(request.scenario, request.selector, request.routing_key or fabula_id)
            now = self.deps.clock.now()
            record = FabulaRecord(
                fabula_id=fabula_id,
                scenario=request.scenario,
                ref=assignment.ref,
                version=assignment.version,
                status="starting",
                registered_at=now,
                updated_at=now,
                assignment=assignment,
                labels=dict(request.labels),
                run_as=request.run_as,
                request_id=request.request_id,
                request_fingerprint=fingerprint,
            )
            try:
                await self.deps.index.insert(record)
                created = True
            except AlreadyExists:
                existing = await self.record(fabula_id)
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise Conflict(f"fabula {fabula_id} was started by another request", code="request_mismatch")
            assignment, created = existing.assignment, False
        context = ExecutionContext(
            run_as=request.run_as, trigger=request.trigger, labels={**request.labels, **reserved_labels(assignment, request.request_id)}
        )
        await self.deps.runtime.start(fabula_id, assignment.ref, request.input, context)
        if created:
            await self.deps.audit(actor, "fabula.started", f"fabula:{fabula_id}", request.request_id, ref=assignment.ref, tag=assignment.tag, arm=assignment.arm)
        return StartResult(fabula_id=fabula_id, ref=assignment.ref, assignment=assignment, created=created)

    # --- reading --------------------------------------------------------------------

    async def record(self, fabula_id: str) -> FabulaRecord:
        record = await self.deps.index.get(fabula_id)
        if record is None:
            raise NotFound(f"fabula {fabula_id} is not indexed; import it if the runtime knows it", code="fabula_not_found")
        return record

    async def get(self, fabula_id: str) -> FabulaDetails:
        record = await self.record(fabula_id)
        try:
            view = await self.deps.runtime.view(fabula_id)
        except FabulaNotFound:
            view = None
        return FabulaDetails(record=record, view=view)

    async def query(self, query: FabulaQuery) -> FabulaPage:
        after = decode_token(query.page_token) if query.page_token else None
        found = await self.deps.index.query(query, query.page_size + 1, after)
        items = found[: query.page_size]
        token = None
        if len(found) > query.page_size:
            last = items[-1]
            moment = last.updated_at if query.order_by == "updated_at" else (last.started_at or last.registered_at)
            token = encode_token((moment.isoformat(), last.fabula_id))
        return FabulaPage(items=tuple(items), next_page_token=token)

    async def all(self, query: FabulaQuery) -> list[FabulaRecord]:
        records: list[FabulaRecord] = []
        page = query.model_copy(update={"page_size": 1000, "page_token": None})
        while True:
            result = await self.query(page)
            records.extend(result.items)
            if result.next_page_token is None:
                return records
            page = page.model_copy(update={"page_token": result.next_page_token})

    async def counts(self, scenario: str | None = None) -> Counts:
        return Counts(items=tuple(await self.deps.index.counts(scenario)))

    async def journal(self, fabula_id: str) -> Journal:
        await self.record(fabula_id)
        observations = []
        after = 0
        while True:
            entries = await self.deps.journal.read(fabula_id, after_version=after)
            if not entries:
                break
            for entry in entries:
                observations.extend(entry.observations)
            after = entries[-1].version
        return Journal(fabula_id=fabula_id, steps=tuple(build_journal(observations)))

    # --- interventions ----------------------------------------------------------------

    async def control(self, actor: Actor, fabula_id: str, request: ControlFabula) -> ControlAck:
        await self.record(fabula_id)
        try:
            await self.deps.runtime.control(fabula_id, request.request_id, str(actor), request.reason, request.action)
        except FabulaNotFound:
            raise NotFound(f"the runtime does not know fabula {fabula_id}", code="fabula_not_found") from None
        await self.deps.audit(actor, f"fabula.control.{request.action.type}", f"fabula:{fabula_id}", request.request_id, reason=request.reason)
        return ControlAck(fabula_id=fabula_id, request_id=request.request_id, outcome=await self.find_outcome(fabula_id, request.request_id))

    async def find_outcome(self, fabula_id: str, request_id: str) -> ControlOutcome | None:
        """From the outcome store, falling back to the journal (and backfilling the store)."""
        outcome = await self.deps.outcomes.get(fabula_id, request_id)
        if outcome is not None:
            return outcome
        after = 0
        while True:
            entries = await self.deps.journal.read(fabula_id, after_version=after)
            if not entries:
                return None
            for entry in entries:
                for found in outcomes_of(fabula_id, entry.version, entry.observations):
                    if found.request_id == request_id:
                        await self.deps.outcomes.record(found)
                        return found
            after = entries[-1].version

    async def outcome(self, fabula_id: str, request_id: str) -> ControlOutcome:
        outcome = await self.find_outcome(fabula_id, request_id)
        if outcome is None:
            raise NotFound(f"no outcome of request {request_id} for fabula {fabula_id} yet", code="outcome_not_found")
        return outcome

    async def analyze_swap(self, fabula_id: str, request: AnalyzeSwap) -> SwapReport:
        record = await self.record(fabula_id)
        target = await self.deps.scenarios.get_version(record.scenario, request.version)
        if target is None:
            raise NotFound(f"{version_ref(record.scenario, request.version)} is not published", code="version_not_found")
        try:
            return await self.deps.runtime.analyze_swap(fabula_id, target.ref, request.mapping, request.context_migration)
        except FabulaNotFound:
            raise NotFound(f"the runtime does not know fabula {fabula_id}", code="fabula_not_found") from None

    # --- repair ---------------------------------------------------------------------

    async def import_fabula(self, actor: Actor, fabula_id: str, request_id: str) -> FabulaRecord:
        """Index a fabula the runtime knows but the index does not (started past the manager)."""
        if await self.deps.index.get(fabula_id) is None:
            try:
                state = await self.deps.runtime.state(fabula_id)
            except FabulaNotFound:
                raise NotFound(f"the runtime does not know fabula {fabula_id}", code="fabula_not_found") from None
            scenario, version = split_ref(state.scenario.ref)
            labels = state.execution_context.labels
            now = self.deps.clock.now()
            record = FabulaRecord(
                fabula_id=fabula_id,
                scenario=scenario,
                ref=state.scenario.ref,
                version=version,
                status=state.status,
                started_at=state.started_at,
                registered_at=now,
                updated_at=state.last_at,
                assignment=assignment_from_labels(labels, state.scenario.ref),
                labels={k: v for k, v in labels.items() if not k.startswith(RESERVED_LABEL_PREFIX)},
                run_as=state.execution_context.run_as,
                origin="imported",
            )
            try:
                await self.deps.index.insert(record)
            except AlreadyExists:
                pass
            await self.deps.audit(actor, "fabula.imported", f"fabula:{fabula_id}", request_id)
        return await self.reconcile(fabula_id)

    async def reconcile(self, fabula_id: str) -> FabulaRecord:
        """Replay the journal into the record and overlay the authoritative view."""
        for _ in range(_CAS_ATTEMPTS):
            record = await self.record(fabula_id)
            updated = await self.projector.replay(record)
            try:
                updated = overlay_view(updated, await self.deps.runtime.view(fabula_id))
            except FabulaNotFound:
                if record.status == "starting" and self.deps.clock.now() - record.registered_at > START_GRACE:
                    updated = updated.model_copy(update={"status": "start_failed", "stale": False})
            if updated == record:
                return record
            try:
                await self.deps.index.replace(updated, record.revision)
            except RevisionConflict:
                continue
            return await self.record(fabula_id)
        raise RevisionConflict(f"index record of {fabula_id} keeps changing")

    async def reconcile_stale(self, limit: int = 100) -> int:
        """Repair stale records and starts that never reached the runtime."""
        stale = await self.deps.index.query(FabulaQuery(stale=True), limit)
        stuck = await self.deps.index.query(FabulaQuery(statuses=("starting",), started_before=self.deps.clock.now() - START_GRACE), limit)
        repaired = 0
        for record in {r.fabula_id: r for r in [*stale, *stuck]}.values():
            if await self.reconcile(record.fabula_id) != record:
                repaired += 1
        return repaired
