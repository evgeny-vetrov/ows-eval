"""The composition root of the in-memory profile.

With `sandbox/` it is the only place that uses in-memory implementations from the
`testing` packages of the engine and the manager: a profile over real storage is
another `build` next to this one, and nothing else changes.
"""

import logging
from dataclasses import dataclass

from ai.gena.services.fabula.engine.testing.expressions import TestOnlyExpressionEngine
from ai.gena.services.fabula.engine.testing.matcher import FilterEventMatcher
from ai.gena.services.fabula.engine.testing.schemas import Draft7SchemaValidator
from ai.gena.services.fabula.manager.api.dispatch import Dispatcher
from ai.gena.services.fabula.manager.api.facade import FabulaApi
from ai.gena.services.fabula.manager.api.openapi import API_VERSION
from ai.gena.services.fabula.manager.deps import Deps
from ai.gena.services.fabula.manager.registry.scenarios import RegistryScenarioRepository
from ai.gena.services.fabula.manager.service import Manager, journal_sink
from ai.gena.services.fabula.manager.system import UuidIds
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
from ai.gena.services.fabula.server.auth import Authenticator, ConfiguredAuthorizer, DevAuthenticator, TokenAuthenticator, parse_actor
from ai.gena.services.fabula.server.background import Background
from ai.gena.services.fabula.server.notifications import NotificationHub, NotifyingJournalSink
from ai.gena.services.fabula.server.operations import Facades, server_operations
from ai.gena.services.fabula.server.sandbox.api import SandboxApi
from ai.gena.services.fabula.server.sandbox.clock import SandboxClock
from ai.gena.services.fabula.server.sandbox.runtime import Sandbox
from ai.gena.services.fabula.server.seed import seed_scenarios
from ai.gena.services.fabula.server.session import ServerInfo, SessionApi
from ai.gena.services.fabula.server.settings import ServerSettings, SettingsError, load_catalog

log = logging.getLogger(__name__)


@dataclass
class Components:
    """Everything the HTTP layer needs, and the lifecycle of what runs in background."""

    settings: ServerSettings
    deps: Deps
    manager: Manager
    hub: NotificationHub
    authenticator: Authenticator
    authorizer: ConfiguredAuthorizer
    dispatcher: Dispatcher
    sandbox: Sandbox
    background: Background
    info: ServerInfo
    ready: bool = False

    async def start(self) -> None:
        if self.settings.seed.scenarios_dir is not None:
            await seed_scenarios(self.manager.registry, self.settings.seed.scenarios_dir)
        await self.sandbox.start()
        self.background.start()
        if self.authenticator.mode == "dev":
            log.warning("authentication is in dev mode: callers name themselves with X-Fabula-Actor")
        self.ready = True

    async def stop(self) -> None:
        self.ready = False
        await self.background.stop()
        await self.sandbox.stop()


def _authenticator(settings: ServerSettings) -> Authenticator:
    auth = settings.auth
    try:
        if auth.mode == "dev":
            return DevAuthenticator(parse_actor(auth.dev_actor))
        return TokenAuthenticator({token.sha256: parse_actor(token.actor) for token in auth.tokens})
    except ValueError as exc:
        raise SettingsError(f"auth: {exc}") from None


def build(settings: ServerSettings, clock: SandboxClock | None = None) -> Components:
    capabilities, events = load_catalog(settings.catalog)
    try:
        authorizer = ConfiguredAuthorizer(settings.auth.grants, settings.auth.roles)
    except ValueError as exc:
        raise SettingsError(f"auth: {exc}") from None
    authenticator = _authenticator(settings)
    expressions, matcher, schemas = TestOnlyExpressionEngine(), FilterEventMatcher(), Draft7SchemaValidator()
    clock = clock or SandboxClock()
    hub = NotificationHub(buffer=settings.stream.buffer, client_queue=settings.stream.client_queue)

    scenario_store = InMemoryScenarioStore()
    index = InMemoryFabulaIndex()
    journal = InMemoryJournalStore()
    outcomes = InMemoryControlOutcomes()
    sandbox = Sandbox(
        expressions=expressions,
        matcher=matcher,
        schemas=schemas,
        scenarios=RegistryScenarioRepository(scenario_store),
        journal=NotifyingJournalSink(journal_sink(journal, index, journal, outcomes), hub, clock),
        settings=settings.sandbox,
        clock=clock,
    )
    deps = Deps(
        runtime=sandbox.host,
        scenarios=scenario_store,
        tags=InMemoryTagStore(),
        index=index,
        journal=journal,
        outcomes=outcomes,
        campaigns=InMemoryCampaignStore(),
        tasks=InMemoryResolutionTaskStore(),
        audit_log=InMemoryAuditLog(),
        capabilities=InMemoryCapabilityDirectory(capabilities),
        events=InMemoryEventDirectory(events),
        expressions=expressions,
        schemas=schemas,
        clock=clock,
        ids=UuidIds(),
        notifier=hub,
    )
    manager = Manager(deps)
    info = ServerInfo(api_version=API_VERSION, storage=settings.storage, auth_mode=authenticator.mode, sandbox=settings.sandbox.expose_api, expressions="test-subset")
    operations = server_operations(sandbox=settings.sandbox.expose_api)
    facades = Facades(operations, FabulaApi(manager), SessionApi(authorizer, info), SandboxApi(sandbox, deps.events))
    return Components(
        settings=settings,
        deps=deps,
        manager=manager,
        hub=hub,
        authenticator=authenticator,
        authorizer=authorizer,
        dispatcher=Dispatcher(facades, authorizer, operations),
        sandbox=sandbox,
        background=Background(manager, settings.background),
        info=info,
    )
