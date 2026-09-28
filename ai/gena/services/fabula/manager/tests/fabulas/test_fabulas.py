import asyncio
from datetime import timedelta

import pytest

from ai.gena.services.fabula.engine.model.context import ExecutionContext
from ai.gena.services.fabula.engine.model.stimuli import Pause, Resume
from ai.gena.services.fabula.manager.model.errors import Conflict, NotFound
from ai.gena.services.fabula.manager.model.fabulas import AnalyzeSwap, ControlFabula, FabulaQuery, Selector
from ai.gena.services.fabula.manager.testing.scenarios import SCENARIO, V1, V2, deliver_merged, finish_call, fleet, hold_calls, publish, start
from ai.gena.services.fabula.manager.testing.stand import ALICE, Stand


def run(coroutine):
    return asyncio.run(coroutine)


def _stand() -> Stand:
    stand = Stand()
    run(publish(stand, V1, "1.0.0"))
    return stand


async def _index_matches_views(stand: Stand) -> None:
    for record in stand.index.all():
        view = await stand.runtime.view(record.fabula_id)
        assert (record.status, record.ref, record.waits) == (view.status, view.scenario_ref, view.waits), record.fabula_id
        assert not record.stale


def test_start_routes_through_trunk_and_indexes_the_fabula():
    stand = _stand()
    hold_calls(stand)
    result = run(start(stand, "order-1", labels={"team": "gena"}, run_as="svc-orders"))
    record = run(stand.index.get(result.fabula_id))
    state = run(stand.runtime.state(result.fabula_id))
    assert result.created and result.ref == "test/order:1.0.0"
    assert (record.status, record.at_rest, [w.node for w in record.waits]) == ("waiting", True, ["/ask"])
    assert (record.assignment.tag, record.labels, record.run_as, record.origin) == ("trunk", {"team": "gena"}, "svc-orders", "manager")
    assert state.execution_context.labels["team"] == "gena"
    assert state.execution_context.labels["fabula.request"] == "order-1"


def test_start_is_idempotent_by_request_id_and_refuses_a_different_body():
    stand = _stand()
    first = run(start(stand, "order-1"))
    again = run(start(stand, "order-1"))
    assert (again.fabula_id, again.created) == (first.fabula_id, False)
    with pytest.raises(Conflict) as error:
        run(start(stand, "order-1", input={"ticket": "other"}))
    assert error.value.code == "request_mismatch"


def test_an_explicit_version_bypasses_the_tags():
    stand = _stand()
    run(publish(stand, V2, "2.0.0"))
    result = run(start(stand, "pinned", selector=Selector(version="2.0.0")))
    assert (result.ref, result.assignment.tag) == ("test/order:2.0.0", None)


def test_reserved_labels_are_refused():
    with pytest.raises(ValueError, match="reserved"):
        run(start(_stand(), "x", labels={"fabula.tag": "mine"}))


def test_the_index_follows_every_transition():
    stand = _stand()

    async def scenario():
        ids = await fleet(stand)
        await _index_matches_views(stand)
        await deliver_merged(stand)
        await _index_matches_views(stand)
        return ids

    ids = run(scenario())
    statuses = {role: run(stand.index.get(fid)).status for role, fid in ids.items()}
    assert statuses == {"at_call": "waiting", "at_event": "completed", "failed": "failed", "done": "completed"}
    failed = run(stand.index.get(ids["failed"]))
    assert (failed.failed_node, failed.last_error.title) == ("/ask", "boom")


def _crash_run(crash_at):
    stand = Stand(crash_at=crash_at)
    run(publish(stand, V1, "1.0.0"))
    run(fleet(stand))
    return stand


BASELINE_POINTS = len(_crash_run(None).world.faults.points)


@pytest.mark.parametrize("crash_at", range(1, BASELINE_POINTS + 1, 3))
def test_the_index_survives_a_host_crash_at_any_point(crash_at):
    stand = _crash_run(crash_at)
    assert stand.world.restarts == 1
    run(_index_matches_views(stand))


def test_a_failing_index_never_stalls_the_fabula_and_reconcile_repairs_it():
    stand = _stand()
    hold_calls(stand)
    fabula_id = run(start(stand, "order-1")).fabula_id
    stand.index.fail_next = 1
    run(finish_call(stand, fabula_id))
    assert run(stand.world.store.with_pending_outbox()) == [], "the outbox was acknowledged"
    record = run(stand.index.get(fabula_id))
    assert record.stale and record.waits[0].node == "/ask", "the record is behind and says so"
    run(deliver_merged(stand))
    assert run(stand.index.get(fabula_id)).journal_version == record.journal_version, "later versions wait for the missing one"
    assert run(stand.manager.fabulas.reconcile_stale()) == 1
    repaired = run(stand.index.get(fabula_id))
    assert (repaired.status, repaired.stale) == ("completed", False)
    run(_index_matches_views(stand))


def test_a_start_that_never_reached_the_runtime_is_marked_and_can_be_retried():
    stand = _stand()
    original = stand.runtime.start

    async def unreachable(*args, **kwargs):
        raise ConnectionError("runtime is down")

    stand.runtime.start = unreachable
    with pytest.raises(ConnectionError):
        run(start(stand, "order-1"))
    fabula_id = run(stand.index.query(FabulaQuery(), 10))[0].fabula_id
    assert run(stand.manager.fabulas.reconcile_stale()) == 0, "young starts are left alone"
    stand.clock.advance(timedelta(minutes=10))
    assert run(stand.manager.fabulas.reconcile_stale()) == 1
    assert run(stand.index.get(fabula_id)).status == "start_failed"
    stand.runtime.start = original
    retried = run(start(stand, "order-1"))
    assert (retried.fabula_id, run(stand.index.get(fabula_id)).status) == (fabula_id, "waiting")


def test_fabulas_started_past_the_manager_are_indexed_or_imported():
    stand = _stand()
    run(stand.world.host.start("direct", "test/order:1.0.0", {"ticket": "T"}, ExecutionContext(labels={"team": "x"})))
    external = run(stand.index.get("direct"))
    assert (external.origin, external.status, external.labels) == ("external", "waiting", {})
    stand.index.fail_next = 1
    run(stand.world.host.start("lost", "test/order:1.0.0", {"ticket": "T"}, ExecutionContext(labels={"team": "y"})))
    assert run(stand.index.get("lost")) is None
    imported = run(stand.manager.fabulas.import_fabula(ALICE, "lost", "import-1"))
    assert (imported.origin, imported.status, imported.labels, imported.stale) == ("imported", "waiting", {"team": "y"}, False)
    run(_index_matches_views(stand))
    with pytest.raises(NotFound):
        run(stand.manager.fabulas.import_fabula(ALICE, "nobody", "import-2"))


def test_queries_filter_count_and_paginate():
    stand = _stand()
    hold_calls(stand)

    async def scenario():
        for i in range(25):
            await start(stand, f"order-{i:02}", labels={"parity": str(i % 2)})
        await publish(stand, V2, "2.0.0")
        await start(stand, "v2", selector=Selector(version="2.0.0"))
        pages, token = [], None
        while True:
            page = await stand.manager.fabulas.query(FabulaQuery(scenario=SCENARIO, page_size=10, page_token=token))
            pages.append(page.items)
            token = page.next_page_token
            if token is None:
                return pages

    pages = run(scenario())
    assert [len(p) for p in pages] == [10, 10, 6]
    ids = [r.fabula_id for p in pages for r in p]
    assert len(set(ids)) == 26 and ids == sorted(ids, reverse=True), "same start time: ordered by id"
    fabulas = stand.manager.fabulas
    assert len(run(fabulas.query(FabulaQuery(versions=("2.0.0",)))).items) == 1
    assert len(run(fabulas.query(FabulaQuery(labels={"parity": "1"}))).items) == 12
    assert len(run(fabulas.query(FabulaQuery(waiting_on="/ask", tag="trunk"))).items) == 25
    counts = run(fabulas.counts(SCENARIO))
    assert [(c.version, c.status, c.count) for c in counts.items] == [("1.0.0", "waiting", 25), ("2.0.0", "waiting", 1)]


def test_controls_report_their_outcome_and_the_journal_backs_the_outcome_store():
    stand = _stand()
    hold_calls(stand)
    fabula_id = run(start(stand, "order-1")).fabula_id
    fabulas = stand.manager.fabulas
    paused = run(fabulas.control(ALICE, fabula_id, ControlFabula(request_id="pause-1", action=Pause(), reason="look")))
    twice = run(fabulas.control(ALICE, fabula_id, ControlFabula(request_id="pause-2", action=Pause())))
    resumed = run(fabulas.control(ALICE, fabula_id, ControlFabula(request_id="resume-1", action=Resume())))
    assert (paused.outcome.accepted, paused.outcome.actor) == (True, "user:alice")
    assert (twice.outcome.accepted, twice.outcome.code) == (False, "invalid_status")
    assert resumed.outcome.accepted
    stand.outcomes._outcomes.clear()
    recovered = run(fabulas.outcome(fabula_id, "pause-2"))
    assert recovered == twice.outcome
    with pytest.raises(NotFound):
        run(fabulas.outcome(fabula_id, "never"))
    assert "fabula.control.pause" in stand.audit.actions(f"fabula:{fabula_id}")


def test_details_journal_and_swap_analysis():
    stand = _stand()
    hold_calls(stand)
    fabula_id = run(start(stand, "order-1")).fabula_id
    run(publish(stand, V2, "2.0.0"))
    details = run(stand.manager.fabulas.get(fabula_id))
    journal = run(stand.manager.fabulas.journal(fabula_id))
    report = run(stand.manager.fabulas.analyze_swap(fabula_id, AnalyzeSwap(version="2.0.0")))
    assert details.view.status == details.record.status == "waiting"
    assert [(s.node, s.status) for s in journal.steps] == [("/prepare", "completed"), ("/ask", "running")]
    assert report.compatible
    with pytest.raises(NotFound):
        run(stand.manager.fabulas.analyze_swap(fabula_id, AnalyzeSwap(version="9.0.0")))
    with pytest.raises(NotFound):
        run(stand.manager.fabulas.get("nobody"))


def test_a_recovered_fabula_loses_its_failed_node():
    from ai.gena.services.fabula.engine.model.stimuli import RetryTask

    stand = _stand()

    async def scenario():
        ids = await fleet(stand)
        await stand.manager.fabulas.control(ALICE, ids["failed"], ControlFabula(request_id="retry", action=RetryTask(node="/ask")))
        return await stand.index.get(ids["failed"])

    record = run(scenario())
    assert (record.status, record.failed_node) == ("waiting", None)
    run(_index_matches_views(stand))
