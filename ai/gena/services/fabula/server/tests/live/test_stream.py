"""Live notifications over server-sent events."""

import asyncio
from datetime import datetime, timezone

from ai.gena.services.fabula.manager.model.audit import ManagerEvent
from ai.gena.services.fabula.manager.testing.scenarios import V1, order
from ai.gena.services.fabula.server.auth import token_digest
from ai.gena.services.fabula.server.testing.harness import make_settings, query, running

STREAM = "/v1/notifications:stream"
ORDER = "/v1/scenarios/test/order"


def run(coroutine):
    return asyncio.run(coroutine)


async def publish_v1(s) -> None:
    await s.post(ORDER + "/versions", {"request_id": "v1", "source": order(V1, "1.0.0")}, expect=201)


async def start(s, name: str) -> str:
    started = await s.post("/v1/fabulas", {"request_id": name, "scenario": "test/order", "input": {"ticket": name}}, expect=201)
    await s.settle()
    return started["fabula_id"]


def test_notifications_arrive_as_they_happen():
    async def scenario():
        async with running() as s:
            async with s.stream(STREAM) as stream:
                assert stream.status == 200 and stream.headers["content-type"].startswith("text/event-stream")
                await publish_v1(s)
                fabula_id = await start(s, "one")
                await stream.wait(lambda: "fabula.changed" in stream.kinds())
            published = next(f for f in stream.frames if f.event == "version.published")
            assert published.data["resource"] == "scenario:test/order" and published.data["kind"] == "version.published"
            assert published.id.startswith(s.components.hub.boot + ".")
            changed = next(f for f in stream.frames if f.event == "fabula.changed")
            assert changed.data["resource"] == f"fabula:{fabula_id}" and changed.data["data"]["journal_version"] == 1
            assert stream.body.startswith("retry: ")
            assert s.components.hub.subscribers == 0, "a closed stream unsubscribes"

    run(scenario())


def test_kinds_and_resources_filter_the_stream():
    async def scenario():
        async with running() as s:
            await publish_v1(s)
            first, second = await start(s, "first"), await start(s, "second")
            async with s.stream(STREAM + "?" + query(kind="fabula.*", resource=f"fabula:{second}")) as stream:
                await s.post(ORDER + "/versions", {"request_id": "again", "source": order(V1.replace("version: 1", "version: 9"), "1.0.1")}, expect=201)
                for fabula_id in (first, second):
                    await s.post(f"/v1/fabulas/{fabula_id}/controls", {"request_id": f"pause-{fabula_id}", "action": {"type": "pause"}}, expect=202)
                await stream.events(1)
                await asyncio.sleep(0.05)
            assert {(f.event, f.data["resource"]) for f in stream.frames} == {("fabula.changed", f"fabula:{second}")}
            assert stream.frames[0].data["data"]["status"] == "paused"

    run(scenario())


def test_a_reconnecting_client_gets_what_it_missed_or_a_reset():
    async def scenario():
        async with running() as s:
            async with s.stream(STREAM) as stream:
                await publish_v1(s)
                [seen] = await stream.events(1)
            fabula_id = await start(s, "missed")
            async with s.stream(STREAM, {"Last-Event-ID": seen.id}) as resumed:
                await resumed.wait(lambda: len(resumed.frames) >= 1)
                await asyncio.sleep(0.05)
            assert {f.event for f in resumed.frames} == {"fabula.changed"}
            assert all(f.data["resource"] == f"fabula:{fabula_id}" for f in resumed.frames)
            async with s.stream(STREAM + "?" + query(last_event_id="another-boot.3")) as restarted:
                [reset] = await restarted.events(1)
            assert reset.event == "reset" and reset.id == s.components.hub.last_id

    run(scenario())


def test_a_client_that_falls_behind_is_told_to_refetch():
    async def scenario():
        async with running(make_settings(stream={"client_queue": 3})) as s:
            async with s.stream(STREAM) as stream:
                at = datetime.now(timezone.utc)
                for i in range(10):  # no await in between: the client cannot keep up
                    s.components.hub.publish(ManagerEvent(kind="test.burst", resource="scenario:test/order", at=at, data={"i": i}))
                s.components.hub.publish(ManagerEvent(kind="test.after", resource="scenario:test/order", at=at))
                await stream.wait(lambda: "test.after" in stream.kinds())
            assert stream.kinds()[0] == "reset"
            assert stream.kinds()[-1] == "test.after" and len(stream.frames) <= 5

    run(scenario())


def only(grants: dict, roles: dict | None = None):
    """Test settings with exactly these grants."""
    settings = make_settings()
    return settings.model_copy(update={"auth": settings.auth.model_copy(update={"grants": grants, "roles": roles or {}})})


def test_an_actor_sees_only_what_it_may_read():
    settings = only({"user:alice": ("admin",), "user:reader": ("catalog",)}, roles={"catalog": ("scenario.read",)})

    async def scenario():
        async with running(settings) as s:
            async with s.stream(STREAM + "?actor=user:reader") as stream:
                await publish_v1(s)
                await start(s, "hidden")
                await stream.events(1)
                await asyncio.sleep(0.05)
            assert stream.kinds() == ["version.published"]

    run(scenario())


def test_idle_streams_get_heartbeats():
    async def scenario():
        async with running() as s:
            async with s.stream(STREAM) as stream:
                await stream.wait(lambda: stream.comments >= 2)

    run(scenario())


def test_streams_take_the_token_from_the_query():
    settings = make_settings(auth={"mode": "tokens", "tokens": [{"sha256": token_digest("s3cret"), "actor": "user:alice"}]})

    async def scenario():
        async with running(settings) as s:
            async with s.stream(STREAM) as anonymous:
                pass
            assert anonymous.status == 401
            async with s.stream(STREAM + "?access_token=s3cret") as stream:
                assert stream.status == 200
            assert (await s.call("GET", "/v1/me?access_token=s3cret")).status_code == 401, "only streams read the query"

    run(scenario())


def test_a_stream_ends_when_the_client_leaves_even_if_writes_are_dropped():
    """ASGI 2.4: Starlette does not watch for the disconnect, the server drops writes."""

    async def scenario():
        async with running() as s:
            stream = s.stream(STREAM, spec_version="2.4")
            async with stream:
                await publish_v1(s)
                await stream.events(1)
                assert s.components.hub.subscribers == 1
                await stream.disconnect()
            assert s.components.hub.subscribers == 0

    run(scenario())


def test_the_cursor_moves_even_when_nothing_is_shown():
    async def scenario():
        async with running() as s:
            async with s.stream(STREAM + "?kind=fabula.*") as quiet:
                await quiet.wait(lambda: quiet.last_id is not None)
                start = quiet.last_id
                assert start == s.components.hub.last_id and quiet.frames == []
                await publish_v1(s)  # filtered out: version.published
                await quiet.wait(lambda: quiet.last_id != start)
            assert quiet.frames == [] and quiet.last_id == s.components.hub.last_id
            # A notification published while the client was away still arrives.
            fabula_id = await start_fabula(s)
            async with s.stream(STREAM + "?kind=fabula.*", {"Last-Event-ID": quiet.last_id}) as back:
                await back.events(1)
            assert back.frames[0].event == "fabula.changed" and back.frames[0].data["resource"] == f"fabula:{fabula_id}"

    run(scenario())


async def start_fabula(s) -> str:
    return await start(s, "while-away")
