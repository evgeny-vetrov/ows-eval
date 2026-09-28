"""Effectful ports. Only the host uses them; the core never sees them.

Results and events reach the host through the `StimulusSink` an adapter was
constructed with. Every delivery is at least once: the host and the core tolerate
duplicates and late arrivals.
"""

from datetime import datetime
from typing import Protocol

from ai.gena.services.fabula.engine.model.commands import InvokeCapability, Subscribe
from ai.gena.services.fabula.engine.model.event import Event
from ai.gena.services.fabula.engine.model.stimuli import Outcome, Stimulus


class StimulusSink(Protocol):
    async def __call__(self, fabula_id: str, stimulus: Stimulus) -> None: ...


class CapabilityInvoker(Protocol):
    """Asynchronous capability ("cube") invocation.

    * `invoke` is idempotent by `invocation_id`: a repeated call with the same id never
      starts a second execution.
    * The result arrives later as `InvocationFinished` with the same `invocation_id`,
      at least once. The `stimulus_id` of a redelivery stays the same.
    * `cancel` is best effort and idempotent; a result may still arrive after it.
    * `request.timeout` is a hint; the core enforces the deadline itself.
    """

    async def invoke(self, fabula_id: str, request: InvokeCapability) -> None: ...

    async def cancel(self, fabula_id: str, invocation_id: str) -> None: ...


class PollingCapabilityInvoker(CapabilityInvoker, Protocol):
    """Optional extension for executors without push delivery."""

    async def poll(self, fabula_id: str, invocation_id: str) -> Outcome | None: ...


class EventSubscriptions(Protocol):
    """Subscriptions in the external event system.

    * `subscribe` is idempotent by `subscription_id`. Repeating it with the same filter
      and a later `expires_at` extends the subscription.
    * Events matching the filter are delivered as `EventDelivered` at least once, with a
      `delivery_key` stable across redeliveries of the same event to the same
      subscription.
    * Events since `request.since` are delivered too (lookback); duplicates are allowed.
    * After `unsubscribe` late deliveries are possible.
    * Order of events for different subjects is not guaranteed.
    * There is no "one-shot" flag: the core unsubscribes explicitly.
    """

    async def subscribe(self, fabula_id: str, request: Subscribe) -> None: ...

    async def unsubscribe(self, fabula_id: str, subscription_id: str) -> None: ...


class EventPublisher(Protocol):
    """`publish` is idempotent by `emit_id`."""

    async def publish(self, emit_id: str, event: Event) -> None: ...


class TimerService(Protocol):
    """Durable timers.

    * `schedule` is idempotent by `timer_id`; the core never reuses an id for another
      instant, a rescheduled deadline gets a new id.
    * When due, `TimerFired` is delivered at least once; `at` is the delivery time.
    * `cancel` is best effort; a late `TimerFired` is ignored by the core.
    """

    async def schedule(self, fabula_id: str, timer_id: str, fire_at: datetime) -> None: ...

    async def cancel(self, fabula_id: str, timer_id: str) -> None: ...


class Clock(Protocol):
    """Host-side wall clock. The core never reads it: time enters as `stimulus.at`."""

    def now(self) -> datetime: ...
