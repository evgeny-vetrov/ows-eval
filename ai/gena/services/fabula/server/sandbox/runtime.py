"""The sandbox: the engine's reference host running inside the server, over in-memory
stores, with simulated capabilities, events and timers.

It is the `FabulaRuntime` of the in-memory profile (`host` satisfies the port) and
what lets the web UI be developed without real executors: a person completes calls,
publishes events and moves the clock through `/v1/sandbox`.
"""

import asyncio
import logging
from datetime import timedelta

from ai.gena.services.fabula.engine.core.interpreter import Interpreter
from ai.gena.services.fabula.engine.core.limits import EngineLimits
from ai.gena.services.fabula.engine.model.event import Event
from ai.gena.services.fabula.engine.model.stimuli import Outcome
from ai.gena.services.fabula.engine.ports.expressions import ExpressionEngine
from ai.gena.services.fabula.engine.ports.matching import EventMatcher
from ai.gena.services.fabula.engine.ports.schemas import SchemaValidator
from ai.gena.services.fabula.engine.ports.storage import JournalSink, ScenarioRepository
from ai.gena.services.fabula.engine.runtime.host import Host
from ai.gena.services.fabula.engine.testing.effects import PENDING, InMemoryCapabilityInvoker, InMemoryEventBus, InMemoryTimerService
from ai.gena.services.fabula.engine.testing.stores import InMemoryBlobStore, InMemoryFabulaStore
from ai.gena.services.fabula.manager.model.errors import Conflict, ManagerError, NotFound
from ai.gena.services.fabula.server.sandbox import models
from ai.gena.services.fabula.server.sandbox.clock import SandboxClock
from ai.gena.services.fabula.server.sandbox.effects import Behaviours, DeliveryWorker, SignallingQueue
from ai.gena.services.fabula.server.settings import SandboxSettings

log = logging.getLogger(__name__)


class NotSettled(ManagerError):
    """The change is made, but what it caused has not reached the fabulas in time."""

    status = 503
    code = "not_settled"


class DeliveryFailed(ManagerError):
    """The change is made, but the host refused what it caused (see dead letters)."""

    status = 500
    code = "delivery_failed"


class Sandbox:
    def __init__(
        self,
        *,
        expressions: ExpressionEngine,
        matcher: EventMatcher,
        schemas: SchemaValidator,
        scenarios: ScenarioRepository,
        journal: JournalSink,
        settings: SandboxSettings = SandboxSettings(),
        clock: SandboxClock | None = None,
        limits: EngineLimits = EngineLimits(),
    ):
        self.settings = settings
        self.clock = clock or SandboxClock()
        self.queue = SignallingQueue()
        self.invoker = InMemoryCapabilityInvoker(self.clock, self.queue)
        self.invoker.behaviours = Behaviours(settings.behaviours, self._complete_later)
        self.bus = InMemoryEventBus(self.clock, self.queue, matcher)
        self.timers = InMemoryTimerService(self.clock, self.queue)
        self.store = InMemoryFabulaStore()
        self.host = Host(
            interpreter=Interpreter(expressions=expressions, matcher=matcher, schemas=schemas, limits=limits),
            store=self.store,
            scenarios=scenarios,
            invoker=self.invoker,
            subscriptions=self.bus,
            publisher=self.bus,
            timers=self.timers,
            journal=journal,
            blobs=InMemoryBlobStore(),
            clock=self.clock,
        )
        self.worker = DeliveryWorker(self.queue, self.host.deliver, self.clock, attempts=settings.delivery_attempts)
        self._ticker: asyncio.Task | None = None
        self._later: set[asyncio.TimerHandle] = set()

    # --- lifecycle ------------------------------------------------------------------

    async def start(self) -> None:
        await self.host.recover()
        self.worker.start()
        if self._ticker is None:
            self._ticker = asyncio.create_task(self._tick(), name="sandbox-timers")

    async def stop(self) -> None:
        for handle in self._later:
            handle.cancel()
        self._later.clear()
        ticker, self._ticker = self._ticker, None
        if ticker is not None:
            ticker.cancel()
            await asyncio.gather(ticker, return_exceptions=True)
        await self.worker.stop()

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(self.settings.tick_seconds)
            try:
                self.timers.fire_due()
            except Exception:  # the ticker outlives any single failure
                log.exception("firing timers failed")

    def _complete_later(self, delay: float, invocation_id: str, outcome: Outcome) -> None:
        loop = asyncio.get_running_loop()

        def fire() -> None:
            self._later.discard(handle)
            self.invoker.complete(invocation_id, outcome)

        handle = loop.call_later(delay, fire)
        self._later.add(handle)

    async def settle(self, timeout: float | None = None) -> models.SettleResult:
        """Wait until results, events and firings already queued have reached the
        fabulas (and whatever they caused in turn)."""
        settled = await self.worker.settle(self.settings.settle_timeout_seconds if timeout is None else timeout)
        return models.SettleResult(settled=settled, pending_deliveries=self.worker.pending, dead_letters=len(self.worker.dead))

    async def _delivered(self, dead_before: int) -> None:
        """After a change a person made: its effects reached the fabulas, or an error
        says they did not (the change itself stays made; repeating it is harmless)."""
        result = await self.settle()
        if len(self.worker.dead) > dead_before:
            letters = [f"{d.stimulus.stimulus_id}: {d.error}" for d in self.worker.dead[dead_before:]]
            raise DeliveryFailed("the host refused a delivery; it is kept as a dead letter", details={"dead_letters": letters})
        if not result.settled:
            raise NotSettled(
                f"deliveries are still running after {self.settings.settle_timeout_seconds}s; see GET /v1/sandbox",
                details={"pending_deliveries": result.pending_deliveries},
            )

    # --- what a person does ---------------------------------------------------------

    def invocation(self, invocation_id: str) -> models.Invocation:
        execution = self.invoker.executions.get(invocation_id)
        if execution is None:
            raise NotFound(f"no invocation {invocation_id}", code="invocation_not_found")
        finished = execution.outcome is not PENDING
        state = "cancelled" if execution.cancelled and not finished else "finished" if finished else "pending"
        return models.Invocation(
            invocation_id=invocation_id,
            fabula_id=execution.fabula_id,
            capability_ref=execution.request.capability_ref,
            input=execution.request.input,
            state=state,
            outcome=execution.outcome if finished else None,
        )

    def invocations(self, query: models.InvocationQuery) -> list[models.Invocation]:
        found = []
        for invocation_id, execution in self.invoker.executions.items():
            if query.fabula_id is not None and execution.fabula_id != query.fabula_id:
                continue
            if query.capability is not None and execution.request.capability_ref != query.capability:
                continue
            invocation = self.invocation(invocation_id)
            if query.state is None or invocation.state == query.state:
                found.append(invocation)
        return found

    async def complete(self, invocation_id: str, outcome: Outcome) -> models.Invocation:
        current = self.invocation(invocation_id)
        if current.outcome is not None:
            if current.outcome == outcome:
                return current
            raise Conflict(f"invocation {invocation_id} already finished with another outcome", code="invocation_finished")
        dead_before = len(self.worker.dead)
        self.invoker.complete(invocation_id, outcome)
        await self._delivered(dead_before)
        return self.invocation(invocation_id)

    async def publish(self, event: Event) -> models.SandboxEvent:
        dead_before = len(self.worker.dead)
        self.bus.publish_external(event)
        published = models.SandboxEvent(at=self.bus.log[-1][0], origin="external", event=event)
        await self._delivered(dead_before)
        return published

    def events(self, query: models.EventQuery) -> list[models.SandboxEvent]:
        from_fabulas = {event.id for event in self.bus.published.values()}
        items = [
            models.SandboxEvent(at=at, origin="fabula" if event.id in from_fabulas else "external", event=event)
            for at, event in self.bus.log
            if query.type is None or event.type == query.type
        ]
        return items[-query.limit :]

    async def advance(self, by: timedelta) -> models.SandboxStatus:
        dead_before = len(self.worker.dead)
        self.clock.advance(by)
        self.timers.fire_due()
        await self._delivered(dead_before)
        return self.status()

    def status(self) -> models.SandboxStatus:
        counts = {"pending": 0, "finished": 0, "cancelled": 0}
        for invocation_id in self.invoker.executions:
            counts[self.invocation(invocation_id).state] += 1
        timers = sorted(
            (models.Timer(timer_id=timer_id, fabula_id=fabula_id, fire_at=fire_at) for timer_id, (fabula_id, fire_at) in self.timers.scheduled.items()),
            key=lambda timer: (timer.fire_at, timer.timer_id),
        )
        dead = [
            models.DeadLetter(fabula_id=d.fabula_id, stimulus_id=d.stimulus.stimulus_id, stimulus_kind=d.stimulus.kind, error=d.error, at=d.at)
            for d in self.worker.dead
        ]
        return models.SandboxStatus(
            now=self.clock.now(),
            clock_offset=self.clock.offset,
            pending_deliveries=self.worker.pending,
            invocations=counts,
            timers=tuple(timers),
            active_subscriptions=len(self.bus.active()),
            dead_letters=tuple(dead),
        )
