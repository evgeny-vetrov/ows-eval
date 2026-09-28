"""Scenario registry: validation, publication and the life cycle of versions."""

from ai.gena.services.fabula.engine.dsl.loader import LoadResult, load_scenario
from ai.gena.services.fabula.engine.model.catalog import CapabilityDescriptor, EventTypeDescriptor
from ai.gena.services.fabula.engine.model.scenario import ScenarioSnapshot
from ai.gena.services.fabula.manager.deps import Deps
from ai.gena.services.fabula.manager.model.common import Actor, split_ref, version_ref
from ai.gena.services.fabula.manager.model.errors import AlreadyExists, Conflict, InvalidInput, NotFound, Unprocessable
from ai.gena.services.fabula.manager.model.resolution import SetResolutionPolicy
from ai.gena.services.fabula.manager.model.scenarios import (
    CreateScenario,
    DeprecateVersion,
    PublishVersion,
    ScenarioInfo,
    ScenarioList,
    ScenarioVersion,
    UpdateScenario,
    ValidationResult,
    VersionList,
)
from ai.gena.services.fabula.manager.model.tags import TRUNK, Allocation
from ai.gena.services.fabula.manager.ports.stores import ScenarioStore
from ai.gena.services.fabula.manager.registry.tags import write_tag


class RegistryScenarioRepository:
    """Published versions as the engine's `ScenarioRepository`. Deprecated versions still
    resolve: running fabulas and migrations back to them need their snapshots."""

    def __init__(self, store: ScenarioStore):
        self.store = store

    async def get(self, ref: str) -> ScenarioSnapshot | None:
        scenario, version = split_ref(ref)
        found = await self.store.get_version(scenario, version)
        return found.snapshot if found is not None else None


class ScenarioRegistry:
    def __init__(self, deps: Deps):
        self.deps = deps

    # --- scenarios ------------------------------------------------------------------

    async def create_scenario(self, actor: Actor, request: CreateScenario) -> ScenarioInfo:
        now = self.deps.clock.now()
        fields = request.model_dump(include={"title", "description", "owners", "labels", "links"})
        info = ScenarioInfo(scenario=request.scenario, created_at=now, created_by=str(actor), updated_at=now, **fields)
        try:
            await self.deps.scenarios.create_scenario(info)
        except AlreadyExists:
            existing = await self.deps.scenarios.get_scenario(request.scenario)
            if existing is not None and existing.model_dump(include=set(fields)) == info.model_dump(include=set(fields)):
                return existing
            raise AlreadyExists(f"scenario {request.scenario} already exists", code="scenario_exists")
        await self.deps.audit(actor, "scenario.created", f"scenario:{request.scenario}", request.request_id)
        return info

    async def get_scenario(self, scenario: str) -> ScenarioInfo:
        info = await self.deps.scenarios.get_scenario(scenario)
        if info is None:
            raise NotFound(f"scenario {scenario} does not exist", code="scenario_not_found")
        return info

    async def list_scenarios(self, namespace: str | None = None) -> ScenarioList:
        return ScenarioList(items=tuple(await self.deps.scenarios.list_scenarios(namespace)))

    async def update_scenario(self, actor: Actor, scenario: str, request: UpdateScenario) -> ScenarioInfo:
        info = await self.get_scenario(scenario)
        changes = request.model_dump(exclude={"request_id", "expected_revision"}, exclude_none=True)
        return await self._update(actor, info, changes, request.expected_revision, request.request_id, "scenario.updated")

    async def set_policy(self, actor: Actor, scenario: str, request: SetResolutionPolicy) -> ScenarioInfo:
        info = await self.get_scenario(scenario)
        return await self._update(actor, info, {"resolution_policy": request.policy}, request.expected_revision, request.request_id, "scenario.policy_set")

    async def _update(self, actor: Actor, info: ScenarioInfo, changes: dict, expected: int | None, request_id: str, action: str) -> ScenarioInfo:
        revision = info.revision if expected is None else expected
        updated = info.model_copy(update={**changes, "updated_at": self.deps.clock.now()})
        await self.deps.scenarios.update_scenario(updated, revision)
        await self.deps.audit(actor, action, f"scenario:{info.scenario}", request_id, fields=sorted(changes))
        return await self.get_scenario(info.scenario)

    # --- versions -------------------------------------------------------------------

    def _load(self, source: str) -> LoadResult:
        return load_scenario(
            source,
            capabilities=self.deps.capabilities,
            events=self.deps.events,
            expressions=self.deps.expressions,
            schemas=self.deps.schemas,
            limits=self.deps.profile_limits,
        )

    def validate(self, source: str) -> ValidationResult:
        result = self._load(source)
        snapshot = result.snapshot
        return ValidationResult(
            ok=result.ok, ref=snapshot.ref if snapshot else None, digest=snapshot.digest if snapshot else None, diagnostics=result.diagnostics
        )

    async def publish(self, actor: Actor, scenario: str, request: PublishVersion) -> ScenarioVersion:
        result = self._load(request.source)
        if not result.ok:
            diagnostics = [d.model_dump(mode="json") for d in result.diagnostics]
            raise Unprocessable("the scenario is invalid", code="invalid_scenario", details={"diagnostics": diagnostics})
        snapshot = result.snapshot
        declared, version = split_ref(snapshot.ref)
        if declared != scenario:
            raise InvalidInput(f"the document declares {declared}, not {scenario}", code="scenario_mismatch")
        existing = await self.deps.scenarios.get_version(scenario, version)
        if existing is not None:
            return self._same_or_conflict(existing, snapshot)
        if await self.deps.scenarios.get_scenario(scenario) is None:
            await self.create_scenario(actor, CreateScenario(request_id=request.request_id, scenario=scenario, owners=(actor.id,)))
        published = ScenarioVersion(
            scenario=scenario,
            version=version,
            ref=snapshot.ref,
            digest=snapshot.digest,
            notes=request.notes,
            published_at=self.deps.clock.now(),
            published_by=str(actor),
            source=request.source,
            snapshot=snapshot,
        )
        try:
            await self.deps.scenarios.add_version(published)
        except AlreadyExists:
            return self._same_or_conflict(await self.deps.scenarios.get_version(scenario, version), snapshot)
        if await self.deps.tags.get(scenario, TRUNK) is None:
            await write_tag(self.deps, actor, scenario, TRUNK, Allocation.single(version), "create", "first published version", request.request_id, None)
        await self.deps.audit(actor, "version.published", f"scenario:{scenario}", request.request_id, version=version, digest=snapshot.digest)
        await self.deps.notify("version.published", f"scenario:{scenario}", version=version)
        return published

    @staticmethod
    def _same_or_conflict(existing: ScenarioVersion, snapshot: ScenarioSnapshot) -> ScenarioVersion:
        if existing.digest == snapshot.digest:
            return existing
        raise Conflict(
            f"{existing.ref} is already published with other content; publish it under a new version. "
            "The digest also covers the pinned capability and event descriptors, so the same text "
            "conflicts after a catalog change.",
            code="version_exists",
        )

    async def get_version(self, scenario: str, version: str) -> ScenarioVersion:
        found = await self.deps.scenarios.get_version(scenario, version)
        if found is None:
            raise NotFound(f"{version_ref(scenario, version)} is not published", code="version_not_found")
        return found

    async def list_versions(self, scenario: str) -> VersionList:
        await self.get_scenario(scenario)
        return VersionList(items=tuple(v.summary() for v in await self.deps.scenarios.list_versions(scenario)))

    async def active_version(self, scenario: str, version: str) -> ScenarioVersion:
        found = await self.get_version(scenario, version)
        if found.status != "active":
            raise Conflict(f"{found.ref} is deprecated", code="version_deprecated")
        return found

    async def deprecate(self, actor: Actor, scenario: str, version: str, request: DeprecateVersion) -> ScenarioVersion:
        found = await self.get_version(scenario, version)
        if found.status == "deprecated":
            return found
        tagged = [t.tag for t in await self.deps.tags.list(scenario) if version in t.allocation.versions]
        if tagged:
            raise Conflict(f"{found.ref} is still tagged: {', '.join(tagged)}", code="version_tagged")
        updated = found.model_copy(update={"status": "deprecated", "deprecated_at": self.deps.clock.now(), "deprecated_by": str(actor)})
        await self.deps.scenarios.replace_version(updated)
        await self.deps.audit(actor, "version.deprecated", f"scenario:{scenario}", request.request_id, version=version, reason=request.reason)
        return updated

    # --- catalogs -------------------------------------------------------------------

    async def capabilities(self, query: str | None = None) -> list[CapabilityDescriptor]:
        return await self.deps.capabilities.list(query)

    async def event_types(self, query: str | None = None) -> list[EventTypeDescriptor]:
        return await self.deps.events.list(query)
