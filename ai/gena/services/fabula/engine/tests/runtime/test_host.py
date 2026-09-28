import asyncio
from datetime import timedelta

import pytest

from ai.gena.services.fabula.engine.core.interpreter import CoreError
from ai.gena.services.fabula.engine.core.journal import build_journal
from ai.gena.services.fabula.engine.model.context import ExecutionContext
from ai.gena.services.fabula.engine.model.stimuli import Control, OutcomeOk, Pause, Resume
from ai.gena.services.fabula.engine.runtime.host import UnknownScenario
from ai.gena.services.fabula.engine.testing.effects import PENDING
from ai.gena.services.fabula.engine.testing.kit import scenario_text
from ai.gena.services.fabula.engine.testing.pilot import PILOT, PILOT_REF, drive_pilot, pilot_world
from ai.gena.services.fabula.engine.testing.runner import event
from ai.gena.services.fabula.engine.testing.world import World

EXPECTED_OUTPUT = {"ticket": "T-42", "pr": "PR-T-42", "approved": True}


def run(coroutine):
    return asyncio.run(coroutine)


def _pilot(crash_at=None):
    world = pilot_world(crash_at=crash_at)
    run(drive_pilot(world))
    return world


BASELINE = _pilot()


def test_pilot_runs_to_completion_in_memory():
    world = BASELINE
    view = run(world.host.view("pilot-1"))
    assert (view.status, view.output, view.current_nodes, view.waits) == ("completed", EXPECTED_OUTPUT, (), ())
    refs = [e.request.capability_ref for e in world.invoker.executions.values()]
    assert refs == ["agent.run:1@gena", "http.healthcheck:1@platform", "human.approve:1@gena", "tracker.comment:1@tracker"]
    comment = next(e for e in world.invoker.executions.values() if e.request.capability_ref == "tracker.comment:1@tracker")
    assert comment.request.input == {"ticket": "T-42", "text": "Deployed PR-T-42 after a health check"}
    assert comment.request.context.run_as == "gena-bot"
    [(fabula_id, _, kind)] = world.terminal
    assert (fabula_id, kind) == ("pilot-1", "completed")
    assert world.bus.active() == [] and world.timers.scheduled == {}
    steps = [(s.node, s.status) for s in build_journal(world.journal.observations("pilot-1"))]
    assert steps == [
        ("/runAgent", "completed"),
        ("/awaitMerge", "completed"),
        ("/soak", "completed"),
        ("/healthCheck", "completed"),
        ("/healthCheck/try/probe", "completed"),
        ("/approve", "completed"),
        ("/decide", "completed"),
        ("/comment", "completed"),
    ]


def test_pilot_took_three_days_of_fake_time():
    journal = build_journal(BASELINE.journal.observations("pilot-1"))
    soak = next(s for s in journal if s.node == "/soak")
    assert soak.finished_at - soak.started_at == timedelta(days=3)


CRASH_POINTS = len(BASELINE.faults.points)


def _redelivery(observation) -> bool:
    return observation.kind == "stimulus_ignored" and observation.reason == "duplicate"


@pytest.mark.parametrize("crash_at", range(1, CRASH_POINTS + 1))
def test_pilot_survives_a_crash_at_every_step(crash_at):
    world = _pilot(crash_at)
    assert world.faults.crashed_at is not None
    assert world.restarts == 1
    view = run(world.host.view("pilot-1"))
    assert (view.status, view.output) == ("completed", EXPECTED_OUTPUT)
    # Every logical invocation executed exactly once; repeats only hit the idempotent port.
    assert list(world.invoker.executions) == list(BASELINE.invoker.executions)
    assert all(e.outcome is not None for e in world.invoker.executions.values())
    # The terminal notification may repeat after a crash, but always for one version.
    [(fabula_id, _, kind)] = set(world.terminal)
    assert (fabula_id, kind) == ("pilot-1", "completed")
    # A crash may only add records of redelivered duplicates; everything else is identical.
    journal = [o.model_dump_json() for o in world.journal.observations("pilot-1") if not _redelivery(o)]
    assert journal == [o.model_dump_json() for o in BASELINE.journal.observations("pilot-1")]
    assert world.bus.active() == [] and world.timers.scheduled == {}


@pytest.mark.parametrize("first", range(1, CRASH_POINTS, 7))
def test_pilot_survives_repeated_crashes_including_during_recovery(first):
    world = _pilot({first, first + 1, first + 2, first + 5})
    assert len(world.faults.crashes) >= 3
    view = run(world.host.view("pilot-1"))
    assert (view.status, view.output) == ("completed", EXPECTED_OUTPUT)
    assert list(world.invoker.executions) == list(BASELINE.invoker.executions)


def test_crash_points_cover_every_command():
    assert sum(point.startswith("command") for point in BASELINE.faults.points) >= 10
    assert {"applied", "saved", "loaded", "journaled", "acknowledged"} <= set(BASELINE.faults.points)


def test_parallel_stimuli_for_one_fabula_are_serialized():
    world = World()
    world.scenarios.publish(
        world.kit.snapshot(
            scenario_text(
                """\
                do:
                  - par:
                      fork:
                        branches:
                          - a: {call: 'echo:1@test'}
                          - b: {call: 'echo:1@test'}
                          - c: {call: 'echo:1@test'}
                """
            )
        )
    )
    world.invoker.behaviours["echo:1@test"] = lambda request: PENDING

    async def scenario():
        await world.host.start("f", "test/scenario:1.0.0", None, ExecutionContext())
        for invocation_id in list(world.invoker.executions):
            world.invoker.complete(invocation_id, OutcomeOk(output=invocation_id[-8:]))
        deliveries = list(world.queue.items)
        world.queue.items.clear()
        await asyncio.gather(*(world.host.deliver(fabula_id, stimulus) for fabula_id, stimulus in deliveries))
        return await world.host.view("f")

    view = run(scenario())
    assert view.status == "completed"
    assert world.store.saves == 3


def test_two_hosts_on_one_store_resolve_conflicts_by_retrying():
    world = World()
    world.scenarios.publish(world.kit.snapshot(scenario_text("do:\n  - rest: {wait: PT1H}\n")))
    other = world.new_host()

    async def scenario():
        await world.host.start("f", "test/scenario:1.0.0", None, ExecutionContext())
        pause = Control(stimulus_id="c1", at=world.clock.now(), request_id="c1", actor="ops", action=Pause())
        resume = Control(stimulus_id="c2", at=world.clock.now(), request_id="c2", actor="ops", action=Resume())
        await asyncio.gather(world.host.deliver("f", pause), other.deliver("f", resume))
        return await world.host.state("f")

    state = run(scenario())
    decisions = sorted(record.accepted for record in state.recent_controls)
    assert len(state.recent_controls) == 2 and decisions in ([False, True], [True, True])


def test_large_values_are_offloaded_to_the_blob_store():
    world = World(externalize_threshold=1024)
    world.scenarios.publish(world.kit.snapshot(scenario_text("do:\n  - rest: {wait: PT1H}\n  - done: {set: {size: '${ .blob | length }'}}\n")))
    payload = {"blob": "x" * 50_000}

    async def scenario():
        await world.start("f", "test/scenario:1.0.0", payload, ExecutionContext())
        stored = world.store.document("f")
        assert len(str(stored)) < 5_000
        assert len(world.blobs) >= 1
        await world.advance(timedelta(hours=1))
        return await world.host.view("f")

    view = run(scenario())
    assert (view.status, view.output) == ("completed", {"size": 50_000})


def test_start_is_idempotent_and_needs_a_published_scenario():
    world = pilot_world()

    async def scenario():
        with pytest.raises(UnknownScenario):
            await world.host.start("x", "gena/missing:1", {}, ExecutionContext())
        await world.host.start("p", PILOT_REF, {"ticket": "T-1"}, ExecutionContext())
        await world.host.start("p", PILOT_REF, {"ticket": "T-2"}, ExecutionContext())
        return await world.host.state("p")

    state = run(scenario())
    assert state.workflow_input == {"ticket": "T-1"}
    assert len(world.invoker.executions) == 1


def test_core_error_leaves_the_stored_state_and_the_delivery_in_place():
    world = pilot_world()

    class Broken(Exception):
        pass

    async def scenario():
        await world.host.start("p", PILOT_REF, {"ticket": "T-1"}, ExecutionContext())
        before = world.store.document("p")
        original = world.host.interpreter.apply

        def explode(state, stimulus):
            raise CoreError("bug") from Broken()

        world.host.interpreter.apply = explode
        with pytest.raises(CoreError):
            await world.queue.pump(world.host.deliver)
        assert world.store.document("p") == before
        assert len(world.queue.items) == 1
        world.host.interpreter.apply = original
        await world.queue.pump(world.host.deliver)
        return await world.host.view("p")

    assert run(scenario()).status == "waiting"


def test_host_controls_and_swaps_versions_through_the_repository():
    world = pilot_world()
    v2 = world.kit.snapshot(PILOT.replace("version: '1.0.0'", "version: '2.0.0'").replace("Deployed ", "Shipped "))
    world.scenarios.publish(v2)

    async def scenario():
        await world.start("p", PILOT_REF, {"ticket": "T-7"}, ExecutionContext())
        report = await world.host.analyze_swap("p", v2.ref)
        assert report.applicable and report.compatible
        await world.host.swap_version("p", "swap-1", "alice", "new comment text", v2.ref)
        await world.host.control("p", "pause-1", "alice", "hold on", Pause())
        assert (await world.host.view("p")).status == "paused"
        await world.host.control("p", "resume-1", "alice", "go", Resume())
        world.bus.publish_external(event("vcs.pr.merged", {"pr_id": "PR-T-7"}, id="m"))
        await world.pump()
        await world.advance(timedelta(days=3))
        [approval] = world.invoker.pending("human.approve:1@gena")
        world.invoker.complete(approval, OutcomeOk(output={"approved": True}))
        await world.pump()
        return await world.host.state("p")

    state = run(scenario())
    assert (state.status, state.scenario.ref) == ("completed", "gena/ticket-to-prod:2.0.0")
    comment = next(e for e in world.invoker.executions.values() if e.request.capability_ref == "tracker.comment:1@tracker")
    assert comment.request.input["text"].startswith("Shipped ")
    with pytest.raises(UnknownScenario):
        run(world.host.swap_version("p", "swap-2", "alice", "", "gena/missing:9"))
