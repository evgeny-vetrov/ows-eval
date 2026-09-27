"""Reference asynchronous host.

For every stimulus: load the state (flushing an unacknowledged outbox first), apply the
stimulus, save the new state together with the outbox under optimistic locking,
execute the commands, write the observations, acknowledge the outbox.

A crash between any two steps is survived: after a restart `recover()` flushes pending
outboxes, producers redeliver unacknowledged stimuli, and repeated commands are
harmless because every effect port is idempotent by the command id.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from ai.gena.services.fabula.engine.core.codec import dump_state, load_state
from ai.gena.services.fabula.engine.core.interpreter import Interpreter
from ai.gena.services.fabula.engine.core.projection import FabulaView, project
from ai.gena.services.fabula.engine.core.state import FabulaState
from ai.gena.services.fabula.engine.model.commands import Command
from ai.gena.services.fabula.engine.model.context import ExecutionContext
from ai.gena.services.fabula.engine.model.scenario import ScenarioSnapshot
from ai.gena.services.fabula.engine.model.stimuli import Control, ControlAction, Stimulus, SwapVersion
from ai.gena.services.fabula.engine.model.swap import SwapReport
from ai.gena.services.fabula.engine.ports.blobs import BLOB, LITERAL, externalize, internalize
from ai.gena.services.fabula.engine.ports.effects import CapabilityInvoker, Clock, EventPublisher, EventSubscriptions, TimerService
from ai.gena.services.fabula.engine.ports.storage import (
    BlobStore,
    FabulaNotFound,
    FabulaStore,
    JournalSink,
    Outbox,
    ScenarioRepository,
    VersionConflict,
)


class UnknownScenario(Exception):
    pass


def _no_fault(point: str) -> None:
    return None


class Host:
    def __init__(
        self,
        *,
        interpreter: Interpreter,
        store: FabulaStore,
        scenarios: ScenarioRepository,
        invoker: CapabilityInvoker,
        subscriptions: EventSubscriptions,
        publisher: EventPublisher,
        timers: TimerService,
        journal: JournalSink,
        blobs: BlobStore,
        clock: Clock,
        externalize_threshold: int = 64 * 1024,
        max_conflict_retries: int = 5,
        on_terminal: Callable[[str, int, Command], Awaitable[None]] | None = None,
        fault: Callable[[str], None] = _no_fault,
    ):
        self.interpreter = interpreter
        self.store = store
        self.scenarios = scenarios
        self.invoker = invoker
        self.subscriptions = subscriptions
        self.publisher = publisher
        self.timers = timers
        self.journal = journal
        self.blobs = blobs
        self.clock = clock
        self.externalize_threshold = externalize_threshold
        self.max_conflict_retries = max_conflict_retries
        self.on_terminal = on_terminal
        self.fault = fault
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, fabula_id: str) -> asyncio.Lock:
        lock = self._locks.get(fabula_id)
        if lock is None:
            lock = self._locks[fabula_id] = asyncio.Lock()
        return lock

    # --- API --------------------------------------------------------------------------

    async def start(self, fabula_id: str, scenario_ref: str, input: Any, context: ExecutionContext) -> None:
        """Idempotent: starting a known fabula does nothing."""
        async with self._lock(fabula_id):
            try:
                await self.store.load(fabula_id)
                return
            except FabulaNotFound:
                pass
            scenario = await self._scenario(scenario_ref)
            transition = self.interpreter.start(scenario, input, context, self.clock.now(), fabula_id=fabula_id)
            self.fault("applied")
            outbox = Outbox(version=1, commands=transition.commands, observations=transition.observations)
            await self.store.create(fabula_id, await self._persistable(transition.state), outbox)
            self.fault("saved")
            await self._flush(fabula_id, outbox)

    async def deliver(self, fabula_id: str, stimulus: Stimulus) -> None:
        """`StimulusSink` of the effect ports. Stimuli of one fabula are applied one at a time."""
        async with self._lock(fabula_id):
            for attempt in range(self.max_conflict_retries + 1):
                stored = await self.store.load(fabula_id)
                if stored.outbox is not None:
                    await self._flush(fabula_id, stored.outbox)
                state = await self._restore(stored.document)
                self.fault("loaded")
                transition = self.interpreter.apply(state, stimulus)
                self.fault("applied")
                outbox = Outbox(version=stored.version + 1, commands=transition.commands, observations=transition.observations)
                try:
                    await self.store.save(fabula_id, await self._persistable(transition.state), stored.version, outbox)
                except VersionConflict:
                    if attempt == self.max_conflict_retries:
                        raise
                    continue
                self.fault("saved")
                await self._flush(fabula_id, outbox)
                return

    async def control(self, fabula_id: str, request_id: str, actor: str, reason: str, action: ControlAction) -> None:
        """Submit an intervention. Rights of `actor` are the caller's business."""
        stimulus = Control(stimulus_id=f"control:{request_id}", at=self.clock.now(), request_id=request_id, actor=actor, reason=reason, action=action)
        await self.deliver(fabula_id, stimulus)

    async def analyze_swap(self, fabula_id: str, scenario_ref: str, mapping: dict[str, str] | None = None, context_migration: str | None = None) -> SwapReport:
        scenario = await self._scenario(scenario_ref)
        return self.interpreter.analyze_swap(await self.state(fabula_id), scenario, mapping or {}, context_migration)

    async def swap_version(
        self,
        fabula_id: str,
        request_id: str,
        actor: str,
        reason: str,
        scenario_ref: str,
        mapping: dict[str, str] | None = None,
        context_migration: str | None = None,
    ) -> None:
        scenario = await self._scenario(scenario_ref)
        action = SwapVersion(scenario=scenario, mapping=mapping or {}, context_migration=context_migration)
        await self.control(fabula_id, request_id, actor, reason, action)

    async def recover(self) -> None:
        """Finish the side effects of transitions that were saved but not acknowledged."""
        for fabula_id in await self.store.with_pending_outbox():
            async with self._lock(fabula_id):
                stored = await self.store.load(fabula_id)
                if stored.outbox is not None:
                    await self._flush(fabula_id, stored.outbox)

    async def state(self, fabula_id: str) -> FabulaState:
        return await self._restore((await self.store.load(fabula_id)).document)

    async def view(self, fabula_id: str) -> FabulaView:
        return project(await self.state(fabula_id))

    # --- internals ----------------------------------------------------------------------

    async def _scenario(self, scenario_ref: str) -> ScenarioSnapshot:
        scenario = await self.scenarios.get(scenario_ref)
        if scenario is None:
            raise UnknownScenario(scenario_ref)
        return scenario

    async def _flush(self, fabula_id: str, outbox: Outbox) -> None:
        for index, command in enumerate(outbox.commands):
            await self._execute(fabula_id, outbox.version, command)
            self.fault(f"command {index}: {command.kind}")
        await self.journal.write(fabula_id, outbox.version, outbox.observations)
        self.fault("journaled")
        await self.store.ack_outbox(fabula_id, outbox.version)
        self.fault("acknowledged")

    async def _execute(self, fabula_id: str, version: int, command: Command) -> None:
        kind = command.kind
        if kind == "invoke_capability":
            await self.invoker.invoke(fabula_id, command)
        elif kind == "cancel_invocation":
            await self.invoker.cancel(fabula_id, command.invocation_id)
        elif kind == "start_timer":
            await self.timers.schedule(fabula_id, command.timer_id, command.fire_at)
        elif kind == "cancel_timer":
            await self.timers.cancel(fabula_id, command.timer_id)
        elif kind == "subscribe":
            await self.subscriptions.subscribe(fabula_id, command)
        elif kind == "unsubscribe":
            await self.subscriptions.unsubscribe(fabula_id, command.subscription_id)
        elif kind == "publish_event":
            await self.publisher.publish(command.emit_id, command.event)
        elif self.on_terminal is not None:
            # Terminal notifications carry no id of their own; like every command they
            # may repeat after a crash, and `(fabula_id, version)` identifies them.
            await self.on_terminal(fabula_id, version, command)

    async def _persistable(self, state: FabulaState) -> dict[str, Any]:
        document, blobs = externalize(dump_state(state), self.externalize_threshold)
        for ref, data in blobs.items():
            await self.blobs.put(ref, data)
        return document

    async def _restore(self, document: dict[str, Any]) -> FabulaState:
        fetched: dict[str, bytes] = {}
        pending = _blob_refs(document)
        while pending:
            ref = pending.pop()
            if ref in fetched:
                continue
            fetched[ref] = await self.blobs.get(ref)
            pending |= _blob_refs(json.loads(fetched[ref]))
        return load_state(internalize(document, fetched.__getitem__))


def _blob_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if BLOB in item and set(item) <= {BLOB, "size"}:
                refs.add(item[BLOB])
            elif set(item) == {LITERAL}:
                stack.extend(item[LITERAL].values())
            else:
                stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return refs
