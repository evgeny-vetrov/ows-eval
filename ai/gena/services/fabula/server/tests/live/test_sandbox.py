"""The sandbox runtime: simulated capabilities, events, timers and deliveries."""

import asyncio

from ai.gena.services.fabula.engine.testing.kit import scenario_text
from ai.gena.services.fabula.server.testing.harness import make_settings, running

CALLS = """
do:
  - a: {call: 'echo:1@test', with: {n: 1}}
  - b: {call: 'strict:1@test', with: {x: 2}}
  - c: {call: 'flaky:1@test'}
"""
RESTING = "do:\n  - rest: {wait: PT1H}\n  - done: {set: {rested: true}}\n"
EMITS = """
do:
  - hear:
      listen:
        to:
          one: {with: {type: test.ping, data: {n: 7}}}
  - tell:
      emit:
        event:
          with: {type: test.notice, data: {text: heard}}
"""


def run(coroutine):
    return asyncio.run(coroutine)


async def publish_and_start(s, body: str, name: str = "s") -> str:
    await s.post(f"/v1/scenarios/test/{name}/versions", {"request_id": f"p-{name}", "source": scenario_text(body, name=name)}, expect=201)
    started = await s.post("/v1/fabulas", {"request_id": f"start-{name}", "scenario": f"test/{name}"}, expect=201)
    await s.settle()
    return started["fabula_id"]


async def state_of(s, fabula_id: str) -> str:
    return (await s.get(f"/v1/fabulas/{fabula_id}"))["record"]["status"]


def test_capabilities_behave_as_configured():
    behaviours = {
        "echo:*": {"mode": "echo"},
        "strict:1@test": {"mode": "ok", "output": {"y": 3}},
        "flaky:1@test": {"mode": "error", "error_type": "runtime", "error_title": "down", "after_seconds": 0.05},
    }

    async def scenario():
        async with running(make_settings(sandbox={"behaviours": behaviours})) as s:
            fabula_id = await publish_and_start(s, CALLS)
            calls = (await s.get("/v1/sandbox/invocations", params={"fabula_id": fabula_id}))["items"]
            assert [(c["capability_ref"], c["state"], c["outcome"]) for c in calls] == [
                ("echo:1@test", "finished", {"status": "ok", "output": {"n": 1}}),
                ("strict:1@test", "finished", {"status": "ok", "output": {"y": 3}}),
                ("flaky:1@test", "pending", None),
            ]
            await asyncio.sleep(0.1)
            await s.settle()
            assert await state_of(s, fabula_id) == "failed"
            [late] = (await s.get("/v1/sandbox/invocations", params={"capability": "flaky:1@test"}))["items"]
            assert late["outcome"]["problem"]["title"] == "down"

    run(scenario())


def test_a_person_completes_a_call_once():
    async def scenario():
        async with running() as s:
            fabula_id = await publish_and_start(s, "do:\n  - ask: {call: 'echo:1@test'}\n")
            [call] = (await s.get("/v1/sandbox/invocations", params={"state": "pending"}))["items"]
            complete = {"invocation_id": call["invocation_id"], "outcome": {"status": "ok", "output": "yes"}}
            assert (await s.post("/v1/sandbox/invocations:complete", complete))["state"] == "finished"
            assert (await s.post("/v1/sandbox/invocations:complete", complete))["state"] == "finished", "the same outcome again is harmless"
            other = {**complete, "outcome": {"status": "ok", "output": "no"}}
            assert (await s.post("/v1/sandbox/invocations:complete", other, expect=409))["code"] == "invocation_finished"
            unknown = {**complete, "invocation_id": "nope"}
            assert (await s.post("/v1/sandbox/invocations:complete", unknown, expect=404))["code"] == "invocation_not_found"
            assert await state_of(s, fabula_id) == "completed"

    run(scenario())


def test_events_reach_matching_subscriptions_and_fabulas_publish_theirs():
    async def scenario():
        async with running() as s:
            fabula_id = await publish_and_start(s, EMITS)
            assert (await s.get("/v1/sandbox"))["active_subscriptions"] == 1
            await s.post("/v1/sandbox/events", {"type": "test.ping", "data": {"n": 1}}, expect=201)
            assert await state_of(s, fabula_id) == "waiting", "the filter does not match"
            await s.post("/v1/sandbox/events", {"type": "test.ping", "data": {"n": 7}, "subject": "seven"}, expect=201)
            assert await state_of(s, fabula_id) == "completed"
            events = (await s.get("/v1/sandbox/events"))["items"]
            assert [(e["origin"], e["event"]["type"]) for e in events] == [("external", "test.ping"), ("external", "test.ping"), ("fabula", "test.notice")]
            assert (await s.get("/v1/sandbox/events", params={"type": "test.notice"}))["items"][0]["event"]["data"] == {"text": "heard"}
            unknown = await s.post("/v1/sandbox/events", {"type": "no.such.event"}, expect=422)
            assert unknown["code"] == "unknown_event_type"

    run(scenario())


def test_moving_the_clock_fires_due_timers():
    async def scenario():
        async with running() as s:
            fabula_id = await publish_and_start(s, RESTING)
            status = await s.get("/v1/sandbox")
            assert [t["fabula_id"] for t in status["timers"]] == [fabula_id]
            status = await s.post("/v1/sandbox/clock:advance", {"by": "PT59M"})
            assert status["clock_offset"] == "PT59M" and await state_of(s, fabula_id) == "waiting"
            status = await s.post("/v1/sandbox/clock:advance", {"by": 60})
            assert status["timers"] == [] and await state_of(s, fabula_id) == "completed"
            invalid = await s.post("/v1/sandbox/clock:advance", {"by": "-PT1M"}, expect=400)
            assert invalid["code"] == "invalid_input"

    run(scenario())


def test_the_ticker_fires_timers_that_come_due_in_real_time():
    async def scenario():
        async with running(make_settings(sandbox={"tick_seconds": 0.01})) as s:
            fabula_id = await publish_and_start(s, "do:\n  - rest: {wait: PT0.05S}\n")
            await asyncio.sleep(0.2)
            await s.settle()
            assert await state_of(s, fabula_id) == "completed"

    run(scenario())


def test_a_delivery_the_host_keeps_failing_on_becomes_a_dead_letter():
    async def scenario():
        async with running() as s:
            fabula_id = await publish_and_start(s, "do:\n  - ask: {call: 'echo:1@test'}\n")
            worker = s.sandbox.worker
            worker.backoff = 0

            async def broken(fabula_id, stimulus):
                raise RuntimeError("store down")

            worker.sink = broken
            [call] = (await s.get("/v1/sandbox/invocations"))["items"]
            failed = await s.post("/v1/sandbox/invocations:complete", {"invocation_id": call["invocation_id"], "outcome": {"status": "ok"}}, expect=500)
            assert failed["code"] == "delivery_failed" and "store down" in failed["dead_letters"][0]
            settled = await s.post("/v1/sandbox:settle")
            assert settled == {"settled": True, "pending_deliveries": 0, "dead_letters": 1}
            [dead] = (await s.get("/v1/sandbox"))["dead_letters"]
            assert (dead["fabula_id"], dead["stimulus_kind"], dead["error"]) == (fabula_id, "invocation_finished", "RuntimeError('store down')")
            assert await state_of(s, fabula_id) == "waiting"

    run(scenario())


def test_without_the_sandbox_api_its_routes_do_not_exist():
    async def scenario():
        async with running(make_settings(sandbox={"expose_api": False})) as s:
            missing = await s.call("GET", "/v1/sandbox/invocations", expect=404)
            assert missing.json()["code"] == "route_not_found"
            assert (await s.get("/v1/me"))["server"]["sandbox"] is False
            assert not any(path.startswith("/v1/sandbox") for path in (await s.get("/openapi.json"))["paths"])

    run(scenario())


def test_a_change_whose_deliveries_do_not_finish_in_time_says_so():
    async def scenario():
        async with running(make_settings(sandbox={"settle_timeout_seconds": 0.01})) as s:
            fabula_id = await publish_and_start(s, "do:\n  - ask: {call: 'echo:1@test'}\n")
            worker = s.sandbox.worker
            deliver = worker.sink

            async def slow(fabula_id, stimulus):
                await asyncio.sleep(0.1)
                await deliver(fabula_id, stimulus)

            worker.sink = slow
            [call] = (await s.get("/v1/sandbox/invocations"))["items"]
            late = await s.post("/v1/sandbox/invocations:complete", {"invocation_id": call["invocation_id"], "outcome": {"status": "ok"}}, expect=503)
            assert late["code"] == "not_settled" and late["pending_deliveries"] == 1
            assert (await s.post("/v1/sandbox:settle"))["settled"] is False
            await asyncio.sleep(0.15)
            assert await state_of(s, fabula_id) == "completed"

    run(scenario())
