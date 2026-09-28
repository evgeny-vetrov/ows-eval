"""Authentication at the edge, rights by role, and `/v1/me`."""

import asyncio

from ai.gena.services.fabula.server.auth import token_digest
from ai.gena.services.fabula.server.testing.harness import make_settings, running

TOKENS = {
    "mode": "tokens",
    "tokens": [
        {"sha256": token_digest("alice-token"), "actor": "user:alice"},
        {"sha256": token_digest("viewer-token"), "actor": "service:dashboard"},
    ],
}


def run(coroutine):
    return asyncio.run(coroutine)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_tokens_identify_the_actor():
    async def scenario():
        async with running(make_settings(auth=TOKENS)) as s:
            missing = await s.call("GET", "/v1/me", expect=401)
            assert missing.json()["code"] == "unauthenticated" and missing.headers["www-authenticate"].startswith("Bearer")
            assert missing.headers["x-fabula-operation"] == "getMe"
            assert (await s.call("GET", "/v1/me", headers=bearer("nope"), expect=401)).json()["detail"] == "the token is not valid"
            me = await s.get("/v1/me", headers=bearer("alice-token"))
            assert me["actor"] == {"id": "alice", "kind": "user"} and me["roles"] == ["admin"] and "sandbox.operate" in me["permissions"]
            assert me["server"] == {"api_version": "1.0.0", "storage": "memory", "auth_mode": "tokens", "sandbox": True, "expressions": "test-subset"}
            # The dev header means nothing here.
            assert (await s.call("GET", "/v1/me", actor="user:alice", expect=401)).status_code == 401
            assert (await s.call("GET", "/healthz", expect=200)).json() == {"status": "ok"}
            assert (await s.call("GET", "/openapi.json", expect=200)).status_code == 200

    run(scenario())


def test_rights_follow_the_roles():
    async def scenario():
        async with running(make_settings(auth=TOKENS)) as s:
            viewer = bearer("viewer-token")
            me = await s.get("/v1/me", headers=viewer)
            assert me["roles"] == ["viewer"] and me["permissions"] == ["scenario.read", "fabula.read", "migration.read", "audit.read"]
            assert (await s.call("GET", "/v1/scenarios", headers=viewer)).status_code == 200
            denied = await s.call("POST", "/v1/scenarios", json={"request_id": "c", "scenario": "test/order"}, headers=viewer, expect=403)
            assert denied.json()["code"] == "forbidden" and "scenario.write" in denied.json()["detail"]
            assert (await s.call("GET", "/v1/sandbox", headers=viewer, expect=403)).json()["code"] == "forbidden"
            assert (await s.call("POST", "/v1/scenarios", json={"request_id": "c", "scenario": "test/order"}, headers=bearer("alice-token"))).status_code == 201

    run(scenario())


def test_dev_mode_trusts_the_actor_header():
    async def scenario():
        async with running() as s:
            assert (await s.get("/v1/me"))["actor"] == {"id": "alice", "kind": "user"}, "the configured default"
            agent = await s.get("/v1/me", actor="agent:resolver-1")
            assert agent["actor"]["kind"] == "agent" and agent["roles"] == ["resolver"]
            bad = await s.call("GET", "/v1/me", actor="robot:x", expect=401)
            assert bad.json()["code"] == "unauthenticated" and "www-authenticate" not in bad.headers

    run(scenario())


def test_an_actor_without_grants_may_still_ask_who_it_is():
    settings = make_settings()
    settings = settings.model_copy(update={"auth": settings.auth.model_copy(update={"grants": {}})})

    async def scenario():
        async with running(settings) as s:
            me = await s.get("/v1/me")
            assert me["roles"] == [] and me["permissions"] == []
            assert (await s.call("GET", "/v1/scenarios", expect=403)).json()["code"] == "forbidden"

    run(scenario())
