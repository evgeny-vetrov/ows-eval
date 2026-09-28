"""Live notifications for the web: the manager's events and a `fabula.changed` per
journaled transition, fanned out to stream subscribers.

Each notification gets the id `<boot>.<seq>`. A client reconnecting with the last id
it saw gets what it missed while that is still buffered; otherwise (buffer overrun,
server restart, a client too slow to keep up) it is told it lost track and refetches.
"""

import asyncio
import logging
import re
import uuid
from collections import deque
from dataclasses import dataclass

from ai.gena.services.fabula.engine.model.observations import Observation
from ai.gena.services.fabula.engine.ports.effects import Clock
from ai.gena.services.fabula.engine.ports.storage import JournalSink
from ai.gena.services.fabula.manager.model.audit import ManagerEvent
from ai.gena.services.fabula.manager.ports.system import Notifier

log = logging.getLogger(__name__)

FABULA_CHANGED = "fabula.changed"


@dataclass(frozen=True)
class Notification:
    id: str
    seq: int
    event: ManagerEvent


class Subscription:
    def __init__(self, hub: "NotificationHub", limit: int):
        self._hub = hub
        self._limit = limit
        self._items: deque[Notification] = deque()
        self._signal = asyncio.Event()
        self._lost = False

    def offer(self, notification: Notification) -> None:
        if len(self._items) >= self._limit:
            self._items.clear()
            self._lost = True
        else:
            self._items.append(notification)
        self._signal.set()

    def mark_lost(self) -> None:
        self._lost = True
        self._signal.set()

    async def next(self, timeout: float) -> tuple[bool, list[Notification]] | None:
        """`(lost, notifications)`, or None when nothing came within `timeout`.
        `lost` means notifications before these were dropped for this client."""
        if not self._items and not self._lost:
            try:
                await asyncio.wait_for(self._signal.wait(), timeout)
            except asyncio.TimeoutError:
                return None
        self._signal.clear()
        lost, self._lost = self._lost, False
        items = list(self._items)
        self._items.clear()
        return lost, items

    def close(self) -> None:
        self._hub.unsubscribe(self)


class NotificationHub:
    """The manager's `Notifier`, keeping a buffer for reconnecting clients."""

    def __init__(self, buffer: int = 1000, client_queue: int = 1000, boot: str | None = None):
        self.boot = boot or uuid.uuid4().hex[:12]
        self._buffer: deque[Notification] = deque(maxlen=buffer)
        self._client_queue = client_queue
        self._seq = 0
        self._subscribers: set[Subscription] = set()

    @property
    def last_id(self) -> str:
        return f"{self.boot}.{self._seq}"

    @property
    def subscribers(self) -> int:
        return len(self._subscribers)

    async def notify(self, event: ManagerEvent) -> None:
        self.publish(event)

    def publish(self, event: ManagerEvent) -> Notification:
        self._seq += 1
        notification = Notification(id=f"{self.boot}.{self._seq}", seq=self._seq, event=event)
        self._buffer.append(notification)
        for subscriber in list(self._subscribers):
            subscriber.offer(notification)
        return notification

    def subscribe(self, last_event_id: str | None = None) -> Subscription:
        """A subscription to what comes next, plus what came after `last_event_id`."""
        subscription = Subscription(self, self._client_queue)
        if last_event_id:
            missed = self._after(last_event_id)
            if missed is None:
                subscription.mark_lost()
            for notification in missed or ():
                subscription.offer(notification)
        self._subscribers.add(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        self._subscribers.discard(subscription)

    def _after(self, last_event_id: str) -> list[Notification] | None:
        boot, _, seq = last_event_id.partition(".")
        if boot != self.boot or not re.fullmatch(r"[0-9]{1,18}", seq) or int(seq) > self._seq:
            return None
        after = int(seq)
        oldest = self._buffer[0].seq if self._buffer else self._seq + 1
        if after + 1 < oldest:
            return None
        return [n for n in self._buffer if n.seq > after]


class NotifyingJournalSink:
    """Wraps the host's journal sink: after a transition is journaled, announces it as
    `fabula.changed` so the web can refetch the fabula. At least once, like the
    journal itself."""

    def __init__(self, inner: JournalSink, notifier: Notifier, clock: Clock):
        self.inner = inner
        self.notifier = notifier
        self.clock = clock

    async def write(self, fabula_id: str, version: int, observations: tuple[Observation, ...]) -> None:
        await self.inner.write(fabula_id, version, observations)
        data = {"journal_version": version, "observations": [o.kind for o in observations]}
        statuses = [o.status for o in observations if o.kind == "status_changed"]
        if statuses:
            data["status"] = statuses[-1]
        try:
            await self.notifier.notify(ManagerEvent(kind=FABULA_CHANGED, resource=f"fabula:{fabula_id}", at=self.clock.now(), data=data))
        except Exception:  # a notification never breaks the transition that caused it
            log.exception("notification for %s failed", fabula_id)
