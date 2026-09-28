"""What the server does on its own: seeding, the example configuration, agents that
resolve migration conflicts, and jobs that outlive their failures."""

import asyncio
from pathlib import Path

import pytest

import ai.gena.services.fabula.server as server_package
from ai.gena.services.fabula.manager.testing.scenarios import V1, V3, order
from ai.gena.services.fabula.server.background import Background
from ai.gena.services.fabula.server.composition import build
from ai.gena.services.fabula.server.seed import SeedError, seed_scenarios
from ai.gena.services.fabula.server.settings import BackgroundSettings, load_settings
from ai.gena.services.fabula.server.testing.harness import make_settings, running

ORDER = "/v1/scenarios/test/order"


def run(coroutine):
    return asyncio.run(coroutine)


def examples() -> Path:
    try:
        import yatest.common

        return Path(yatest.common.source_path("ai/gena/services/fabula/server/examples"))
    except ImportError:
        return Path(server_package.__file__).parent / "examples"


def test_the_example_configuration_starts_a_seeded_server():
    settings = load_settings(examples() / "dev.yaml")

    async def scenario():
        async with running(settings) as s:
            versions = await s.get("/v1/scenarios/demo/order/versions")
            assert [v["version"] for v in versions["items"]] == ["1.0.0", "1.1.0", "2.0.0"]
            assert (await s.get("/v1/scenarios/demo/order/tags/trunk"))["allocation"]["arms"][0]["version"] == "1.0.0"
            me = await s.get("/v1/me", headers={"X-Fabula-Actor": "user:someone"})
            assert me["roles"] == ["admin"] and me["server"]["auth_mode"] == "dev"
            diff = await s.get("/v1/scenarios/demo/order/diff", params={"from_version": "1.0.0", "to_version": "1.1.0"})
            assert diff["suggested_mapping"] == {"/shipment": "/awaitShipment"}

    run(scenario())


def test_seeding_is_idempotent_and_reports_bad_documents(tmp_path):
    (tmp_path / "a.yaml").write_text(order(V1, "1.0.0"))
    components = build(make_settings())

    async def scenario():
        first = await seed_scenarios(components.manager.registry, tmp_path)
        again = await seed_scenarios(components.manager.registry, tmp_path)
        assert first == again == ["test/order:1.0.0"]
        (tmp_path / "b.yaml").write_text(order("do:\n  - rest: {wait: P1M}\n", "2.0.0"))
        with pytest.raises(SeedError, match=r"b.yaml: the scenario is invalid\n  .*duration"):
            await seed_scenarios(components.manager.registry, tmp_path)
        (tmp_path / "b.yaml").write_text("do: []\n")
        with pytest.raises(SeedError, match="no document.namespace"):
            await seed_scenarios(components.manager.registry, tmp_path)

    run(scenario())


def test_an_agent_inside_the_server_resolves_a_delegated_conflict():
    settings = make_settings(background={"agents": [{"actor_id": "hint-resolver", "interval_seconds": 0.01}]})

    async def scenario():
        async with running(settings) as s:
            await s.post(ORDER + "/versions", {"request_id": "v1", "source": order(V1, "1.0.0")}, expect=201)
            started = await s.post("/v1/fabulas", {"request_id": "o", "scenario": "test/order", "input": {"ticket": "T"}}, expect=201)
            await s.settle()
            [call] = (await s.get("/v1/sandbox/invocations", params={"state": "pending"}))["items"]
            await s.post("/v1/sandbox/invocations:complete", {"invocation_id": call["invocation_id"], "outcome": {"status": "ok", "output": {}}})
            await s.post(ORDER + "/versions", {"request_id": "v3", "source": order(V3, "3.0.0")}, expect=201)
            await s.call("PUT", ORDER + "/resolution-policy", json={"request_id": "p", "policy": {"mode": "agent_applies"}}, expect=200)
            moved = await s.post(ORDER + "/tags/trunk:move", {"request_id": "ship", "version": "3.0.0"})
            migration = f"/v1/migrations/{moved['campaign_id']}"
            await s.post(migration + "/decisions", {"request_id": "d", "decisions": [{"target": {"verdicts": ["blocked"]}, "action": "delegate_to_agent"}]})
            for _ in range(200):
                if (await s.get(migration))["status"] == "done":
                    break
                await asyncio.sleep(0.01)
            assert (await s.get(migration))["status"] == "done"
            assert (await s.get(f"/v1/fabulas/{started['fabula_id']}"))["record"]["version"] == "3.0.0"
            [task] = (await s.get("/v1/agentic-resolutions"))["items"]
            assert task["status"] == "applied" and task["proposed_by"] == "hint-resolver"

    run(scenario())


def test_a_job_outlives_its_failures():
    components = build(make_settings())
    background = Background(components.manager, BackgroundSettings(reconcile_seconds=0))
    calls = []

    async def flaky() -> None:
        calls.append(len(calls))
        if len(calls) < 3:
            raise RuntimeError("not yet")

    background.jobs = {"flaky": (0.001, flaky)}

    async def scenario():
        background.start()
        for _ in range(500):
            if len(calls) >= 4:
                break
            await asyncio.sleep(0.001)
        await background.stop()

    run(scenario())
    assert len(calls) >= 4


def test_background_jobs_follow_the_settings():
    components = build(make_settings())
    agents = [{"actor_id": "a"}, {"actor_id": "b", "pool": "urgent"}]
    background = Background(components.manager, BackgroundSettings.model_validate({"reconcile_seconds": 5, "agents": agents}))
    assert list(background.jobs) == ["reconcile", "agent:a", "agent:b"]
    assert background.jobs["reconcile"][0] == 5
