"""Ports shared by the manager services, plus the small helpers every service needs."""

import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from ai.gena.services.fabula.engine.dsl.compiler import ProfileLimits
from ai.gena.services.fabula.engine.dsl.graph import Graph
from ai.gena.services.fabula.engine.dsl.loader import compile_snapshot
from ai.gena.services.fabula.engine.model.scenario import ScenarioSnapshot
from ai.gena.services.fabula.engine.ports.effects import Clock
from ai.gena.services.fabula.engine.ports.expressions import ExpressionEngine
from ai.gena.services.fabula.engine.ports.schemas import SchemaValidator
from ai.gena.services.fabula.manager.model.audit import AuditRecord, ManagerEvent
from ai.gena.services.fabula.manager.model.common import Actor
from ai.gena.services.fabula.manager.ports.catalogs import CapabilityDirectory, EventDirectory
from ai.gena.services.fabula.manager.ports.runtime import FabulaRuntime
from ai.gena.services.fabula.manager.ports.stores import (
    AuditLog,
    CampaignStore,
    ControlOutcomes,
    FabulaIndex,
    JournalReader,
    ResolutionTaskStore,
    ScenarioStore,
    TagStore,
)
from ai.gena.services.fabula.manager.ports.system import IdSource, Notifier

log = logging.getLogger(__name__)

_GRAPH_CACHE_SIZE = 64


@dataclass
class Deps:
    runtime: FabulaRuntime
    scenarios: ScenarioStore
    tags: TagStore
    index: FabulaIndex
    journal: JournalReader
    outcomes: ControlOutcomes
    campaigns: CampaignStore
    tasks: ResolutionTaskStore
    audit_log: AuditLog
    capabilities: CapabilityDirectory
    events: EventDirectory
    expressions: ExpressionEngine
    schemas: SchemaValidator
    clock: Clock
    ids: IdSource
    notifier: Notifier
    profile_limits: ProfileLimits = ProfileLimits()
    _graphs: OrderedDict = field(default_factory=OrderedDict, repr=False)

    def graph(self, snapshot: ScenarioSnapshot) -> Graph:
        """Compiled graph of a pinned snapshot, memoized by digest."""
        graph = self._graphs.get(snapshot.digest)
        if graph is None:
            graph = compile_snapshot(snapshot, expressions=self.expressions, schemas=self.schemas, limits=self.profile_limits)
            self._graphs[snapshot.digest] = graph
            if len(self._graphs) > _GRAPH_CACHE_SIZE:
                self._graphs.popitem(last=False)
        return graph

    async def audit(self, actor: Actor | str, action: str, resource: str, request_id: str = "", **details: Any) -> None:
        await self.audit_log.append(
            AuditRecord(
                record_id=self.ids.new("audit"),
                at=self.clock.now(),
                actor=str(actor),
                action=action,
                resource=resource,
                request_id=request_id,
                details=details,
            )
        )

    async def notify(self, kind: str, resource: str, **data: Any) -> None:
        try:
            await self.notifier.notify(ManagerEvent(kind=kind, resource=resource, at=self.clock.now(), data=data))
        except Exception:  # a notification never breaks the operation that caused it
            log.exception("notification %s for %s failed", kind, resource)
