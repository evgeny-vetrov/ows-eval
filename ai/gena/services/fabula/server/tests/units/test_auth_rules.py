import asyncio

import pytest

from ai.gena.services.fabula.manager.model.common import Actor
from ai.gena.services.fabula.manager.ports.system import PERMISSIONS
from ai.gena.services.fabula.server.auth import (
    SERVER_PERMISSIONS,
    ConfiguredAuthorizer,
    Credentials,
    DevAuthenticator,
    TokenAuthenticator,
    Unauthenticated,
    parse_actor,
    token_digest,
)
from ai.gena.services.fabula.server.composition import build
from ai.gena.services.fabula.server.settings import SettingsError
from ai.gena.services.fabula.server.testing.harness import make_settings


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.mark.parametrize(
    "text, actor",
    [("user:alice", Actor(id="alice")), ("alice", Actor(id="alice")), ("agent:resolver-1", Actor(id="resolver-1", kind="agent")), (" service:ci ", Actor(id="ci", kind="service"))],
)
def test_actors_parse(text, actor):
    assert parse_actor(text) == actor


@pytest.mark.parametrize("text", ["", "robot:x", "user:", "user:a b", "user:" + "x" * 200])
def test_malformed_actors_are_refused(text):
    with pytest.raises(ValueError):
        parse_actor(text)


def test_every_matching_grant_contributes_roles():
    authorizer = ConfiguredAuthorizer({"*": ("viewer",), "user:*": ("author",), "user:alice": ("operator", "author")})
    alice = Actor(id="alice")
    assert authorizer.roles_of(alice) == ("viewer", "author", "operator")
    assert run(authorizer.allowed(alice, "fabula.control", "fabula:f-1")) and not run(authorizer.allowed(alice, "resolution.work", "*"))
    assert authorizer.roles_of(Actor(id="bot", kind="agent")) == ("viewer",)
    assert ConfiguredAuthorizer({"*": ("admin",)}).permissions_of(alice) == SERVER_PERMISSIONS
    assert set(SERVER_PERMISSIONS) == {*PERMISSIONS, "sandbox.operate"}


def test_roles_and_grants_are_checked():
    with pytest.raises(ValueError, match="unknown permissions: fly"):
        ConfiguredAuthorizer({}, {"pilot": ("fly",)})
    with pytest.raises(ValueError, match="unknown roles: pilot"):
        ConfiguredAuthorizer({"*": ("pilot",)})
    with pytest.raises(SettingsError, match="unknown roles"):
        build(make_settings(auth={"grants": {"*": ["pilot"]}}))
    with pytest.raises(SettingsError, match="auth: 'nobody here' is not an actor"):
        build(make_settings(auth={"dev_actor": "nobody here"}))


def test_tokens_and_the_query():
    authenticator = TokenAuthenticator({token_digest("t"): Actor(id="alice")})
    assert run(authenticator.authenticate(Credentials(headers={"authorization": "bearer t"}))) == Actor(id="alice")
    with pytest.raises(Unauthenticated):
        run(authenticator.authenticate(Credentials(query={"access_token": "t"})))
    assert run(authenticator.authenticate(Credentials(query={"access_token": "t"}, query_allowed=True))) == Actor(id="alice")
    with pytest.raises(Unauthenticated, match="required"):
        run(authenticator.authenticate(Credentials(headers={"authorization": "Basic dDp0"})))


def test_dev_mode():
    authenticator = DevAuthenticator(Actor(id="dev"))
    assert run(authenticator.authenticate(Credentials())) == Actor(id="dev")
    assert run(authenticator.authenticate(Credentials(query={"actor": "agent:a"}, query_allowed=True))) == Actor(id="a", kind="agent")
    assert run(authenticator.authenticate(Credentials(query={"actor": "agent:a"}))) == Actor(id="dev")
