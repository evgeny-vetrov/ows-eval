"""The manager: every service wired over one set of ports."""

from ai.gena.services.fabula.engine.ports.storage import JournalSink
from ai.gena.services.fabula.manager.deps import Deps
from ai.gena.services.fabula.manager.diff.service import DiffService
from ai.gena.services.fabula.manager.fabulas.indexing import IndexProjector, ManagerJournalSink
from ai.gena.services.fabula.manager.fabulas.service import FabulaService
from ai.gena.services.fabula.manager.migrations.service import MigrationService
from ai.gena.services.fabula.manager.ports.stores import ControlOutcomes, FabulaIndex, JournalReader
from ai.gena.services.fabula.manager.registry.scenarios import ScenarioRegistry
from ai.gena.services.fabula.manager.registry.tags import TagService
from ai.gena.services.fabula.manager.resolution.service import ResolutionService


def journal_sink(inner: JournalSink, index: FabulaIndex, journal: JournalReader, outcomes: ControlOutcomes) -> ManagerJournalSink:
    """The `JournalSink` to give the host so that the index and control outcomes follow
    every transition. `journal` reads what `inner` writes."""
    return ManagerJournalSink(inner, IndexProjector(index, journal), outcomes)


class Manager:
    def __init__(self, deps: Deps):
        self.deps = deps
        self.registry = ScenarioRegistry(deps)
        self.tags = TagService(deps, self.registry.active_version)
        self.fabulas = FabulaService(deps, self.tags, IndexProjector(deps.index, deps.journal))
        self.diffs = DiffService(deps)
        self.migrations = MigrationService(deps, self.registry, self.fabulas, self.diffs)
        self.resolution = ResolutionService(deps, self.migrations, self.diffs)
        self.tags.proposer = self.migrations.propose_for_tag
        self.migrations.delegate = self.resolution.create_tasks
