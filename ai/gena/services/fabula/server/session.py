"""`GET /v1/me`: who is calling, what they may do and what this server offers, so the
web UI can hide what the actor cannot use."""

from typing import Literal

from ai.gena.services.fabula.engine.model.base import Frozen
from ai.gena.services.fabula.manager.api.operations import Operation
from ai.gena.services.fabula.manager.model.common import Actor
from ai.gena.services.fabula.manager.ports.system import Authorizer
from ai.gena.services.fabula.server.auth import SERVER_PERMISSIONS


class ServerInfo(Frozen):
    api_version: str
    storage: Literal["memory"]
    auth_mode: str
    # /v1/sandbox is available.
    sandbox: bool
    # `test-subset`: the engine's jq-like test evaluator stands in for jq.
    expressions: Literal["jq", "test-subset"]


class Me(Frozen):
    actor: Actor
    roles: tuple[str, ...]
    # Rights on every resource (`*`).
    permissions: tuple[str, ...]
    server: ServerInfo


SESSION_TAG = {"name": "session", "description": "The caller and the server."}

SESSION_OPERATIONS: tuple[Operation, ...] = (
    Operation("get_me", "GET", "/v1/me", None, "session", "Who is calling, their rights and what the server offers", Me),
)


class SessionApi:
    def __init__(self, authorizer: Authorizer, info: ServerInfo):
        self.authorizer = authorizer
        self.info = info

    async def get_me(self, actor: Actor, path, query: None, body: None) -> Me:
        permissions = tuple([p for p in SERVER_PERMISSIONS if await self.authorizer.allowed(actor, p, "*")])
        roles_of = getattr(self.authorizer, "roles_of", None)
        return Me(actor=actor, roles=roles_of(actor) if roles_of else (), permissions=permissions, server=self.info)
