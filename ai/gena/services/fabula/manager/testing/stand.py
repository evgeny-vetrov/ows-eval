"""A test stand: the engine's in-memory world with the manager on top.

The host of the world resolves scenarios from the registry and writes its journal
through `ManagerJournalSink`, as a production host would.
"""

from ai.gena.services.fabula.engine.model.catalog import CapabilityDescriptor, EventTypeDescriptor
from ai.gena.services.fabula.engine.testing.kit import STANDARD_CAPABILITIES, STANDARD_EVENTS, Kit
from ai.gena.services.fabula.engine.testing.world import World
from ai.gena.services.fabula.manager.deps import Deps
from ai.gena.services.fabula.manager.model.common import Actor
from ai.gena.services.fabula.manager.registry.scenarios import RegistryScenarioRepository
from ai.gena.services.fabula.manager.service import Manager, journal_sink
from ai.gena.services.fabula.manager.testing.catalogs import InMemoryCapabilityDirectory, InMemoryEventDirectory
from ai.gena.services.fabula.manager.testing.stores import (
    InMemoryAuditLog,
    InMemoryCampaignStore,
    InMemoryControlOutcomes,
    InMemoryFabulaIndex,
    InMemoryJournalStore,
    InMemoryResolutionTaskStore,
    InMemoryScenarioStore,
    InMemoryTagStore,
)
from ai.gena.services.fabula.manager.testing.system import RecordingNotifier, SequentialIds

ALICE = Actor(id="alice")
AGENT = Actor(id="resolver-1", kind="agent")


class WorldRuntime:
    """`FabulaRuntime` over the world: mutations run through `World.safely` (a crashed
    host is rebuilt and recovered) and pump the deliveries they cause."""

    def __init__(self, world: World):
        self.world = world

    async def start(self, fabula_id, scenario_ref, input, context) -> None:
        await self.world.safely(lambda: self.world.host.start(fabula_id, scenario_ref, input, context))
        await self.world.pump()

    async def control(self, fabula_id, request_id, actor, reason, action) -> None:
        await self.world.safely(lambda: self.world.host.control(fabula_id, request_id, actor, reason, action))
        await self.world.pump()

    async def swap_version(self, fabula_id, request_id, actor, reason, scenario_ref, mapping=None, context_migration=None) -> None:
        await self.world.safely(lambda: self.world.host.swap_version(fabula_id, request_id, actor, reason, scenario_ref, mapping, context_migration))
        await self.world.pump()

    async def analyze_swap(self, fabula_id, scenario_ref, mapping=None, context_migration=None):
        return await self.world.host.analyze_swap(fabula_id, scenario_ref, mapping, context_migration)

    async def state(self, fabula_id):
        return await self.world.host.state(fabula_id)

    async def view(self, fabula_id):
        return await self.world.host.view(fabula_id)

    async def recover(self) -> None:
        await self.world.safely(lambda: self.world.host.recover(), restart=False)


class Stand:
    def __init__(
        self,
        capabilities: list[CapabilityDescriptor] | None = None,
        events: list[EventTypeDescriptor] | None = None,
        crash_at: int | set[int] | None = None,
    ):
        self.kit = Kit(capabilities, events)
        self.world = World(self.kit, crash_at=crash_at)
        self.scenario_store = InMemoryScenarioStore()
        self.tag_store = InMemoryTagStore()
        self.index = InMemoryFabulaIndex()
        self.journal = InMemoryJournalStore()
        self.outcomes = InMemoryControlOutcomes()
        self.campaign_store = InMemoryCampaignStore()
        self.task_store = InMemoryResolutionTaskStore()
        self.audit = InMemoryAuditLog()
        self.notifier = RecordingNotifier()
        self.world.scenarios = RegistryScenarioRepository(self.scenario_store)
        self.world.journal = journal_sink(self.journal, self.index, self.journal, self.outcomes)
        self.world.host = self.world.new_host()
        self.runtime = WorldRuntime(self.world)
        self.deps = Deps(
            runtime=self.runtime,
            scenarios=self.scenario_store,
            tags=self.tag_store,
            index=self.index,
            journal=self.journal,
            outcomes=self.outcomes,
            campaigns=self.campaign_store,
            tasks=self.task_store,
            audit_log=self.audit,
            capabilities=InMemoryCapabilityDirectory(STANDARD_CAPABILITIES + (capabilities or [])),
            events=InMemoryEventDirectory(STANDARD_EVENTS + (events or [])),
            expressions=self.kit.expressions,
            schemas=self.kit.schemas,
            clock=self.world.clock,
            ids=SequentialIds(),
            notifier=self.notifier,
        )
        self.manager = Manager(self.deps)

    @property
    def clock(self):
        return self.world.clock
