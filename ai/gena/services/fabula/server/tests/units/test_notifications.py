import asyncio
from datetime import datetime, timezone

import pytest

from ai.gena.services.fabula.engine.model.observations import StatusChanged
from ai.gena.services.fabula.manager.api.operations import OPERATIONS
from ai.gena.services.fabula.manager.model.audit import ManagerEvent
from ai.gena.services.fabula.server.notifications import NotificationHub, NotifyingJournalSink
from ai.gena.services.fabula.server.operations import Facades

AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def run(coroutine):
    return asyncio.run(coroutine)


def event(kind: str = "x") -> ManagerEvent:
    return ManagerEvent(kind=kind, resource="scenario:a/b", at=AT)


def drain(subscription):
    return run(subscription.next(0.01))


def test_a_subscriber_resumes_while_the_buffer_holds_what_it_missed():
    hub = NotificationHub(buffer=3, boot="b")
    ids = [hub.publish(event(str(i))).id for i in range(5)]
    assert ids == ["b.1", "b.2", "b.3", "b.4", "b.5"]
    assert drain(hub.subscribe("b.2")) == (False, [n for n in hub._buffer])
    assert drain(hub.subscribe("b.3"))[1][0].id == "b.4"
    assert drain(hub.subscribe("b.5")) is None, "nothing missed, nothing new"
    for gone in ("b.1", "b.9", "x.3", "b.x", "b.\u00b2", "b.-1"):
        assert drain(hub.subscribe(gone)) == (True, []), gone


def test_a_slow_subscriber_loses_track_and_continues():
    hub = NotificationHub(client_queue=2, boot="b")
    subscription = hub.subscribe()
    for i in range(3):
        hub.publish(event(str(i)))
    hub.publish(event("after"))
    lost, items = drain(subscription)
    assert lost and [n.event.kind for n in items] == ["after"]
    subscription.close()
    assert hub.subscribers == 0


class _Journal:
    def __init__(self):
        self.written = []

    async def write(self, fabula_id, version, observations):
        self.written.append((fabula_id, version))


class _Clock:
    def now(self):
        return AT


def test_every_journaled_transition_is_announced_and_a_broken_notifier_is_harmless():
    hub = NotificationHub(boot="b")
    journal = _Journal()
    sink = NotifyingJournalSink(journal, hub, _Clock())
    changed = StatusChanged(at=AT, previous="running", status="waiting")
    run(sink.write("f-1", 2, (changed,)))
    [notification] = hub._buffer
    assert notification.event.kind == "fabula.changed" and notification.event.resource == "fabula:f-1"
    assert notification.event.data == {"journal_version": 2, "observations": ["status_changed"], "status": "waiting"}

    class Broken:
        async def notify(self, event):
            raise RuntimeError("bus down")

    run(NotifyingJournalSink(journal, Broken(), _Clock()).write("f-1", 3, ()))
    assert journal.written == [("f-1", 2), ("f-1", 3)]


def test_facades_must_cover_every_operation_once():
    class One:
        async def list_scenarios(self, *args):
            return None

    with pytest.raises(ValueError, match="0 facades handle create_scenario"):
        Facades(OPERATIONS[:2], One())
    with pytest.raises(ValueError, match="2 facades handle list_scenarios"):
        Facades(OPERATIONS[:1], One(), One())
