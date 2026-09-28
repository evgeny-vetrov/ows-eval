"""An in-memory world for the reference host: ports, stores, fake clock and crashes.

`World.safely` runs an external action; if the host crashes, the world builds a fresh
host over the same stores and ports (the process restarted), recovers it and repeats
the action until it succeeds.
"""

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from ai.gena.services.fabula.engine.core.interpreter import Interpreter
from ai.gena.services.fabula.engine.core.limits import EngineLimits
from ai.gena.services.fabula.engine.runtime.host import Host
from ai.gena.services.fabula.engine.testing.effects import DeliveryQueue, InMemoryCapabilityInvoker, InMemoryEventBus, InMemoryTimerService
from ai.gena.services.fabula.engine.testing.kit import Kit
from ai.gena.services.fabula.engine.testing.stores import (
    FakeClock,
    InMemoryBlobStore,
    InMemoryFabulaStore,
    InMemoryJournal,
    InMemoryScenarioRepository,
)


class SimulatedCrash(Exception):
    pass


class FaultInjector:
    """Crashes the host at the given fault points, counted from 1 across restarts."""

    def __init__(self, crash_at: int | set[int] | None = None):
        self.crash_at = {crash_at} if isinstance(crash_at, int) else set(crash_at or ())
        self.points: list[str] = []
        self.crashes: list[str] = []

    @property
    def crashed_at(self) -> str | None:
        return self.crashes[0] if self.crashes else None

    def __call__(self, point: str) -> None:
        self.points.append(point)
        if len(self.points) in self.crash_at:
            self.crashes.append(point)
            raise SimulatedCrash(point)


class World:
    def __init__(self, kit: Kit | None = None, crash_at: int | set[int] | None = None, limits: EngineLimits = EngineLimits(), externalize_threshold: int = 64 * 1024):
        self.kit = kit or Kit()
        self.limits = limits
        self.externalize_threshold = externalize_threshold
        self.clock = FakeClock()
        self.queue = DeliveryQueue()
        self.invoker = InMemoryCapabilityInvoker(self.clock, self.queue)
        self.timers = InMemoryTimerService(self.clock, self.queue)
        self.bus = InMemoryEventBus(self.clock, self.queue, self.kit.matcher)
        self.store = InMemoryFabulaStore()
        self.scenarios = InMemoryScenarioRepository()
        self.blobs = InMemoryBlobStore()
        self.journal = InMemoryJournal()
        self.faults = FaultInjector(crash_at)
        self.restarts = 0
        self.terminal: list[tuple[str, int, str]] = []
        self.host = self.new_host()

    def new_host(self) -> Host:
        interpreter = Interpreter(expressions=self.kit.expressions, matcher=self.kit.matcher, schemas=self.kit.schemas, limits=self.limits)
        return Host(
            interpreter=interpreter,
            store=self.store,
            scenarios=self.scenarios,
            invoker=self.invoker,
            subscriptions=self.bus,
            publisher=self.bus,
            timers=self.timers,
            journal=self.journal,
            blobs=self.blobs,
            clock=self.clock,
            externalize_threshold=self.externalize_threshold,
            on_terminal=self._on_terminal,
            fault=self.faults,
        )

    async def _on_terminal(self, fabula_id: str, version: int, command) -> None:
        self.terminal.append((fabula_id, version, command.kind))

    async def safely(self, action: Callable[[], Awaitable[Any]], restart: bool = True) -> Any:
        while True:
            try:
                return await action()
            except SimulatedCrash:
                self.host = self.new_host()
                self.restarts += 1
                if restart:
                    await self.safely(lambda: self.host.recover(), restart=False)

    async def pump(self) -> None:
        await self.safely(lambda: self.queue.pump(lambda fabula_id, stimulus: self.host.deliver(fabula_id, stimulus)))

    async def start(self, fabula_id: str, scenario_ref: str, input: Any, context) -> None:
        await self.safely(lambda: self.host.start(fabula_id, scenario_ref, input, context))
        await self.pump()

    async def advance(self, delta: timedelta) -> None:
        self.clock.advance(delta)
        self.timers.fire_due()
        await self.pump()
