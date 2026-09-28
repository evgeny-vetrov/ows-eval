"""Who is calling and what they may do.

`Authenticator` turns credentials into an `Actor`; it is a port, so an OAuth or TVM
adapter replaces the two implementations here without touching the HTTP layer.
`ConfiguredAuthorizer` implements the manager's `Authorizer` with roles granted to
actor patterns.
"""

import hashlib
import hmac
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Protocol

from ai.gena.services.fabula.manager.model.common import Actor
from ai.gena.services.fabula.manager.ports.system import PERMISSIONS

SANDBOX_PERMISSION = "sandbox.operate"
SERVER_PERMISSIONS = (*PERMISSIONS, SANDBOX_PERMISSION)
ACTOR_HEADER = "x-fabula-actor"

BUILTIN_ROLES: dict[str, tuple[str, ...]] = {
    "viewer": ("scenario.read", "fabula.read", "migration.read", "audit.read"),
    "author": ("scenario.read", "scenario.write", "scenario.publish", "tag.write"),
    "operator": ("fabula.read", "fabula.start", "fabula.control", "migration.read", "migration.write", "resolution.approve"),
    "resolver": ("scenario.read", "fabula.read", "migration.read", "resolution.work"),
    "sandbox": (SANDBOX_PERMISSION,),
    "admin": ("*",),
}

_ACTOR = re.compile(r"^(?:(?P<kind>user|service|agent):)?(?P<id>[A-Za-z0-9][A-Za-z0-9_.@\-]{0,127})$")


class Unauthenticated(Exception):
    """Credentials were given but are not valid, or none were given where needed."""


def parse_actor(text: str) -> Actor:
    """`user:alice`, `agent:resolver-1`, `service:ci`; a bare id is a user."""
    match = _ACTOR.match(text.strip())
    if match is None:
        raise ValueError(f"'{text}' is not an actor: expected [user|service|agent:]<id>")
    return Actor(id=match["id"], kind=match["kind"] or "user")


@dataclass(frozen=True)
class Credentials:
    """What the transport hands over: headers with lower-case names and the query.

    Query credentials count only where `query_allowed` (event streams: a browser's
    `EventSource` cannot set headers)."""

    headers: Mapping[str, str] = field(default_factory=dict)
    query: Mapping[str, str] = field(default_factory=dict)
    query_allowed: bool = False


class Authenticator(Protocol):
    mode: str

    async def authenticate(self, credentials: Credentials) -> Actor:
        """The actor, or `Unauthenticated`."""
        ...


class DevAuthenticator:
    """Trusts the caller to say who they are: `X-Fabula-Actor: user:alice` (or
    `?actor=` on streams), otherwise `default`. Never for a shared deployment."""

    mode = "dev"

    def __init__(self, default: Actor):
        self.default = default

    async def authenticate(self, credentials: Credentials) -> Actor:
        claimed = credentials.headers.get(ACTOR_HEADER)
        if claimed is None and credentials.query_allowed:
            claimed = credentials.query.get("actor")
        if claimed is None:
            return self.default
        try:
            return parse_actor(claimed)
        except ValueError as exc:
            raise Unauthenticated(str(exc)) from None


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class TokenAuthenticator:
    """`Authorization: Bearer <token>` (or `?access_token=` on streams); tokens are
    configured by their SHA-256 so the files hold no secrets."""

    mode = "tokens"

    def __init__(self, tokens: Mapping[str, Actor]):
        self.tokens = dict(tokens)

    async def authenticate(self, credentials: Credentials) -> Actor:
        token = None
        scheme, _, value = credentials.headers.get("authorization", "").partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            token = value.strip()
        elif credentials.query_allowed and credentials.query.get("access_token"):
            token = credentials.query["access_token"]
        if token is None:
            raise Unauthenticated("a bearer token is required")
        digest = token_digest(token)
        found = None
        for known, actor in self.tokens.items():  # compare with every entry: no timing hint
            if hmac.compare_digest(known, digest):
                found = actor
        if found is None:
            raise Unauthenticated("the token is not valid")
        return found


class ConfiguredAuthorizer:
    """Rights by role; roles are granted to actor patterns matched against
    `kind:id` (`user:alice`, `agent:*`, `*`). Every matching pattern contributes."""

    def __init__(self, grants: Mapping[str, tuple[str, ...]], roles: Mapping[str, tuple[str, ...]] | None = None):
        self.roles = {**BUILTIN_ROLES, **(roles or {})}
        for role, permissions in self.roles.items():
            unknown = set(permissions) - set(SERVER_PERMISSIONS) - {"*"}
            if unknown:
                raise ValueError(f"role {role} names unknown permissions: {', '.join(sorted(unknown))}")
        for pattern, granted in grants.items():
            unknown = set(granted) - set(self.roles)
            if unknown:
                raise ValueError(f"grant {pattern} names unknown roles: {', '.join(sorted(unknown))}")
        self.grants = dict(grants)

    def roles_of(self, actor: Actor) -> tuple[str, ...]:
        found: list[str] = []
        for pattern, granted in self.grants.items():
            if fnmatchcase(str(actor), pattern):
                found += [role for role in granted if role not in found]
        return tuple(found)

    def permissions_of(self, actor: Actor) -> tuple[str, ...]:
        granted = {p for role in self.roles_of(actor) for p in self.roles[role]}
        if "*" in granted:
            return SERVER_PERMISSIONS
        return tuple(p for p in SERVER_PERMISSIONS if p in granted)

    async def allowed(self, actor: Actor, permission: str, resource: str) -> bool:
        return permission in self.permissions_of(actor)
