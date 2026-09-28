"""Simulated effects of the sandbox over the engine's in-memory ports.

The engine's in-memory invoker, event bus and timers put every result, event and
firing into a delivery queue; here a background worker hands the queue to the host as
it fills, and capabilities behave as configured.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from fnmatch import fnmatchcase

from ai.gena.services.fabula.engine.model.commands import InvokeCapability
from ai.gena.services.fabula.engine.model.problem import OWS_ERRORS, Problem
from ai.gena.services.fabula.engine.model.stimuli import Outcome, OutcomeError, OutcomeOk, Stimulus
from ai.gena.services.fabula.engine.ports.effects import Clock
from ai.gena.services.fabula.engine.testing.effects import PENDING, DeliveryQueue
from ai.gena.services.fabula.server.settings import BehaviourSettings

log = logging.getLogger(__name__)

MANUAL = BehaviourSettings()


class SignallingQueue(DeliveryQueue):
    """A delivery queue that wakes its worker when something arrives."""

    def __init__(self) -> None:
        super().__init__()
        self.ready = asyncio.Event()

    def put(self, fabula_id: str, stimulus: Stimulus) -> None:
        super().put(fabula_id, stimulus)
        self.ready.set()


def outcome_of(behaviour: BehaviourSettings, request: InvokeCapability) -> Outcome | None:
    """The result a behaviour gives, or None when a person gives it."""
    if behaviour.mode == "echo":
        return OutcomeOk(output=request.input)
    if behaviour.mode == "ok":
        return OutcomeOk(output=behaviour.output)
    if behaviour.mode == "error":
        return OutcomeError(problem=Problem.of(OWS_ERRORS + behaviour.error_type, behaviour.error_title))
    return None


class Behaviours:
    """What each capability does, as the engine's in-memory invoker asks for it
    (`behaviours.get(ref, default)(request)`): exact refs first, then globs in order;
    anything else waits for a person."""

    def __init__(self, configured: dict[str, BehaviourSettings], later: Callable[[float, str, Outcome], None]):
        self.configured = dict(configured)
        self.later = later

    def settings_for(self, capability_ref: str) -> BehaviourSettings:
        if capability_ref in self.configured:
            return self.configured[capability_ref]
        for pattern, behaviour in self.configured.items():
            if fnmatchcase(capability_ref, pattern):
                return behaviour
        return MANUAL

    def get(self, capability_ref: str, default: object = None) -> Callable[[InvokeCapability], object]:
        behaviour = self.settings_for(capability_ref)

        def act(request: InvokeCapability) -> object:
            outcome = outcome_of(behaviour, request)
            if outcome is None:
                return PENDING
            if behaviour.after_seconds > 0:
                self.later(behaviour.after_seconds, request.invocation_id, outcome)
                return PENDING
            return outcome

        return act


@dataclass(frozen=True)
class DeadLetter:
    fabula_id: str
    stimulus: Stimulus
    error: str
    at: datetime


class DeliveryWorker:
    """Hands queued stimuli to the host one at a time, in order. A stimulus the host
    keeps failing on is set aside as a dead letter after `attempts` tries so that it
    does not block the others."""

    def __init__(
        self,
        queue: SignallingQueue,
        sink: Callable[[str, Stimulus], Awaitable[None]],
        clock: Clock,
        attempts: int = 3,
        backoff: float = 0.05,
    ):
        self.queue = queue
        self.sink = sink
        self.clock = clock
        self.attempts = attempts
        self.backoff = backoff
        self.dead: list[DeadLetter] = []
        self.delivered = 0
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    @property
    def pending(self) -> int:
        # The stimulus being delivered stays at the head of the queue until it is done.
        return len(self.queue.items)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="sandbox-deliveries")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _run(self) -> None:
        while True:
            await self.queue.ready.wait()
            self.queue.ready.clear()
            await self.drain()

    async def drain(self) -> None:
        async with self._lock:
            while self.queue.items:
                fabula_id, stimulus = self.queue.items[0]
                await self._deliver(fabula_id, stimulus)
                self.queue.items.pop(0)

    async def _deliver(self, fabula_id: str, stimulus: Stimulus) -> None:
        for attempt in range(1, self.attempts + 1):
            try:
                await self.sink(fabula_id, stimulus)
                self.delivered += 1
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("delivery of %s to %s failed (attempt %d): %r", stimulus.stimulus_id, fabula_id, attempt, exc)
                if attempt == self.attempts:
                    self.dead.append(DeadLetter(fabula_id, stimulus, repr(exc), self.clock.now()))
                    log.error("dead letter: %s for %s", stimulus.stimulus_id, fabula_id)
                    return
                await asyncio.sleep(self.backoff * attempt)

    async def settle(self, timeout: float) -> bool:
        """Wait until every queued stimulus is delivered (or set aside). Without a
        running worker the caller drains the queue itself."""
        if self._task is None:
            await self.drain()
            return True
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while self.pending:
            if loop.time() >= deadline:
                return False
            await asyncio.sleep(0.005)
        return True
