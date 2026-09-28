"""The web UI's main path, over HTTP only: validate, publish, start, finish work in the
sandbox, ship a new version and move the running fabulas to it."""

import asyncio

from ai.gena.services.fabula.manager.testing.scenarios import V1, V2, order
from ai.gena.services.fabula.server.testing.harness import running

ORDER = "/v1/scenarios/test/order"
OK = {"status": "ok", "output": {}}


def run(coroutine):
    return asyncio.run(coroutine)


async def finish(s, fabula_id: str) -> None:
    [call] = (await s.get("/v1/sandbox/invocations", params={"state": "pending", "fabula_id": fabula_id}))["items"]
    await s.post("/v1/sandbox/invocations:complete", {"invocation_id": call["invocation_id"], "outcome": OK})


def test_a_new_version_ships_and_the_running_fabulas_move_to_it():
    async def scenario():
        async with running() as s:
            invalid = await s.post("/v1/scenarios:validate", {"source": order("do:\n  - rest: {wait: P1M}\n", "0.1.0")})
            assert not invalid["ok"] and invalid["diagnostics"][0]["code"] == "duration.invalid"
            published = await s.post(ORDER + "/versions", {"request_id": "v1", "source": order(V1, "1.0.0")}, expect=201)
            assert published["ref"] == "test/order:1.0.0"

            ids = []
            for i in range(3):
                started = await s.post("/v1/fabulas", {"request_id": f"order-{i}", "scenario": "test/order", "input": {"ticket": f"T-{i}"}}, expect=201)
                ids.append(started["fabula_id"])
            await s.settle()
            pending = (await s.get("/v1/sandbox/invocations", params={"state": "pending"}))["items"]
            assert sorted(call["fabula_id"] for call in pending) == sorted(ids)

            await finish(s, ids[0])
            await s.post("/v1/sandbox/events", {"type": "vcs.pr.merged", "data": {"pr_id": "PR-1"}}, expect=201)
            assert (await s.get(f"/v1/fabulas/{ids[0]}"))["record"]["status"] == "completed"
            counts = await s.get("/v1/fabulas:count", params={"scenario": "test/order"})
            assert {(row["status"], row["count"]) for row in counts["items"]} == {("completed", 1), ("waiting", 2)}

            await s.post(ORDER + "/versions", {"request_id": "v2", "source": order(V2, "2.0.0")}, expect=201)
            diff = await s.get(ORDER + "/diff", params={"from_version": "1.0.0", "to_version": "2.0.0"})
            assert [(node["path"], node["static_compat"]) for node in diff["nodes"]] == [("/finish", "compatible")]
            moved = await s.post(ORDER + "/tags/trunk:move", {"request_id": "ship", "version": "2.0.0", "reason": "v2"})
            migration = f"/v1/migrations/{moved['campaign_id']}"
            campaign = await s.get(migration)
            assert campaign["status"] == "awaiting_decision" and campaign["counts"]["verdict.in_flight"] == 2
            await s.post(migration + "/decisions", {"request_id": "decide", "decisions": [{"target": {"verdicts": ["in_flight"]}, "action": "migrate"}]})
            applied = await s.post(migration + ":apply", {"request_id": "apply"})
            assert applied["campaign"]["status"] == "done"

            for fid in ids[1:]:
                assert (await s.get(f"/v1/fabulas/{fid}"))["record"]["version"] == "2.0.0"
                await finish(s, fid)
            await s.post("/v1/sandbox/events", {"type": "vcs.pr.merged", "data": {"pr_id": "PR-2"}, "id": "merged-2"}, expect=201)
            for fid in ids[1:]:
                details = await s.get(f"/v1/fabulas/{fid}")
                assert details["record"]["status"] == "completed", details
                assert details["view"]["output"]["version"] == 2
            journal = await s.get(f"/v1/fabulas/{ids[1]}/journal")
            assert [step["node"] for step in journal["steps"]] == ["/prepare", "/ask", "/merged", "/finish"]
            [swap] = (await s.get(f"/v1/fabulas/{ids[1]}"))["record"]["swaps"]
            assert (swap["from_ref"], swap["to_ref"]) == ("test/order:1.0.0", "test/order:2.0.0")
            audit = {record["action"] for record in (await s.get("/v1/audit"))["items"]}
            assert {"version.published", "fabula.started", "tag.move", "migration.created", "migration.applied"} <= audit

    run(scenario())
