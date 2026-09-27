"""In-memory effectful ports with at-least-once delivery.

Results, timer firings and events are appended to a `DeliveryQueue`; a delivery leaves
the queue only after the host accepted it, so a crashed host gets it again after the
restart, with the same `stimulus_id`.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

from ai.gena.services.fabula.engine.model.commands import InvokeCapability, Subscribe
from ai.gena.services.fabula.engine.model.event import Event
from ai.gena.services.fabula.engine.model.stimuli import (
    EventDelivered,
    InvocationFinished,
    Outcome,
    OutcomeOk,
    Stimulus,
    TimerFired,
)
from ai.gena.services.fabula.engine.ports.matching import EventMatcher
from ai.gena.services.fabula.engine.testing.stores import FakeClock

PENDING = object()


class DeliveryQueue:
    def __init__(self) -> None:
        self.items: list[tuple[str, Stimulus]] = []

    def put(self, fabula_id: str, stimulus: Stimulus) -> None:
        self.items.append((fabula_id, stimulus))

    async def pump(self, sink: Callable[[str, Stimulus], Awaitable[None]], limit: int = 100_000) -> int:
        delivered = 0
        while self.items:
            if delivered >= limit:
                raise RuntimeError("delivery queue does not drain")
            fabula_id, stimulus = self.items[0]
            await sink(fabula_id, stimulus)
            self.items.pop(0)
            delivered += 1
        return delivered


@dataclass
class Execution:
    fabula_id: str
    request: InvokeCapability
    outcome: object = PENDING
    cancelled: bool = False


Behaviour = Callable[[InvokeCapability], Outcome | object]


@dataclass
class InMemoryCapabilityInvoker:
    """Fake cubes. A behaviour returns an outcome, or `PENDING` for results given later."""

    clock: FakeClock
    queue: DeliveryQueue
    behaviours: dict[str, Behaviour] = field(default_factory=dict)
    executions: dict[str, Execution] = field(default_factory=dict)
    repeated_invokes: int = 0
    cancels: list[str] = field(default_factory=list)

    async def invoke(self, fabula_id: str, request: InvokeCapability) -> None:
        if request.invocation_id in self.executions:
            self.repeated_invokes += 1
            return
        execution = self.executions[request.invocation_id] = Execution(fabula_id, request)
        behaviour = self.behaviours.get(request.capability_ref, lambda r: OutcomeOk(output=None))
        outcome = behaviour(request)
        if outcome is not PENDING:
            self._finish(execution, outcome)

    async def cancel(self, fabula_id: str, invocation_id: str) -> None:
        self.cancels.append(invocation_id)
        execution = self.executions.get(invocation_id)
        if execution is not None:
            execution.cancelled = True

    def complete(self, invocation_id: str, outcome: Outcome) -> None:
        execution = self.executions[invocation_id]
        if execution.outcome is PENDING:
            self._finish(execution, outcome)

    def pending(self, capability_ref: str) -> list[str]:
        return [i for i, e in self.executions.items() if e.request.capability_ref == capability_ref and e.outcome is PENDING and not e.cancelled]

    def _finish(self, execution: Execution, outcome: Outcome) -> None:
        execution.outcome = outcome
        invocation_id = execution.request.invocation_id
        self.queue.put(
            execution.fabula_id,
            InvocationFinished(stimulus_id=f"result:{invocation_id}", at=self.clock.now(), invocation_id=invocation_id, outcome=outcome),
        )


@dataclass
class InMemoryTimerService:
    clock: FakeClock
    queue: DeliveryQueue
    scheduled: dict[str, tuple[str, datetime]] = field(default_factory=dict)
    fired: set[str] = field(default_factory=set)
    schedule_calls: int = 0

    async def schedule(self, fabula_id: str, timer_id: str, fire_at: datetime) -> None:
        self.schedule_calls += 1
        if timer_id not in self.scheduled and timer_id not in self.fired:
            self.scheduled[timer_id] = (fabula_id, fire_at)

    async def cancel(self, fabula_id: str, timer_id: str) -> None:
        self.scheduled.pop(timer_id, None)

    def fire_due(self) -> int:
        now = self.clock.now()
        due = sorted((fire_at, timer_id) for timer_id, (_, fire_at) in self.scheduled.items() if fire_at <= now)
        for _, timer_id in due:
            fabula_id, _ = self.scheduled.pop(timer_id)
            self.fired.add(timer_id)
            self.queue.put(fabula_id, TimerFired(stimulus_id=f"fired:{timer_id}", at=now, timer_id=timer_id))
        return len(due)


@dataclass
class _Subscription:
    fabula_id: str
    request: Subscribe
    active: bool = True


@dataclass
class InMemoryEventBus:
    """`EventSubscriptions` and `EventPublisher` over one event log, with lookback."""

    clock: FakeClock
    queue: DeliveryQueue
    matcher: EventMatcher
    subscriptions: dict[str, _Subscription] = field(default_factory=dict)
    log: list[tuple[datetime, Event]] = field(default_factory=list)
    published: dict[str, Event] = field(default_factory=dict)
    subscribe_calls: int = 0

    async def subscribe(self, fabula_id: str, request: Subscribe) -> None:
        self.subscribe_calls += 1
        existing = self.subscriptions.get(request.subscription_id)
        if existing is not None:
            existing.request = request
            return
        subscription = self.subscriptions[request.subscription_id] = _Subscription(fabula_id, request)
        for published_at, event in self.log:
            if published_at >= request.since:
                self._offer(subscription, event)

    async def unsubscribe(self, fabula_id: str, subscription_id: str) -> None:
        subscription = self.subscriptions.get(subscription_id)
        if subscription is not None:
            subscription.active = False

    async def publish(self, emit_id: str, event: Event) -> None:
        if emit_id in self.published:
            return
        self.published[emit_id] = event
        self.publish_external(event)

    def publish_external(self, event: Event) -> None:
        self.log.append((self.clock.now(), event))
        for subscription in self.subscriptions.values():
            self._offer(subscription, event)

    def active(self) -> list[str]:
        return [key for key, sub in self.subscriptions.items() if sub.active]

    def _offer(self, subscription: _Subscription, event: Event) -> None:
        request = subscription.request
        expired = request.expires_at is not None and request.expires_at < self.clock.now()
        if not subscription.active or expired or event.type not in request.event_types or not self.matcher.matches(request.filter, event):
            return
        key = f"{request.subscription_id}:{event.id}"
        self.queue.put(
            subscription.fabula_id,
            EventDelivered(stimulus_id=f"delivery:{key}", at=self.clock.now(), subscription_id=request.subscription_id, delivery_key=key, event=event),
        )
