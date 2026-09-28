from collections import OrderedDict

from ai.gena.services.fabula.manager.deps import Deps
from ai.gena.services.fabula.manager.diff.scenario_diff import diff_versions
from ai.gena.services.fabula.manager.model.common import split_ref
from ai.gena.services.fabula.manager.model.diff import ScenarioDiff
from ai.gena.services.fabula.manager.model.errors import NotFound
from ai.gena.services.fabula.manager.model.scenarios import ScenarioVersion

_CACHE_SIZE = 64


class DiffService:
    def __init__(self, deps: Deps):
        self.deps = deps
        self._cache: OrderedDict[tuple[str, str], ScenarioDiff] = OrderedDict()

    async def _version(self, scenario: str, version: str) -> ScenarioVersion:
        found = await self.deps.scenarios.get_version(scenario, version)
        if found is None:
            raise NotFound(f"{scenario}:{version} is not published", code="version_not_found")
        return found

    async def diff(self, scenario: str, from_version: str, to_version: str) -> ScenarioDiff:
        return self.between(await self._version(scenario, from_version), await self._version(scenario, to_version))

    async def diff_refs(self, from_ref: str, to_ref: str) -> ScenarioDiff:
        return await self.diff(split_ref(from_ref)[0], split_ref(from_ref)[1], split_ref(to_ref)[1])

    def between(self, old: ScenarioVersion, new: ScenarioVersion) -> ScenarioDiff:
        key = (old.digest, new.digest)
        found = self._cache.get(key)
        if found is None:
            found = diff_versions(old, new, self.deps.graph(old.snapshot), self.deps.graph(new.snapshot))
            self._cache[key] = found
            if len(self._cache) > _CACHE_SIZE:
                self._cache.popitem(last=False)
        return found
