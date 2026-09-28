from datetime import timedelta

from cases_control import SEQUENCE
from cases_errors import COMMUNICATION, RETRY_SCENARIO

from ai.gena.services.fabula.engine.core.journal import build_journal, current_waits
from ai.gena.services.fabula.engine.core.projection import project
from ai.gena.services.fabula.engine.model.stimuli import Pause, SkipTask
from ai.gena.services.fabula.engine.testing.runner import T0, Act, Case, Finish, Fire, run_case


def test_journal_has_node_visit_attempt_times_input_output_and_error():
    script = [
        Finish(node="/guard/try/ask", error=COMMUNICATION, after=timedelta(seconds=2)),
        Fire(node="/guard", role="retry"),
        Finish(node="/guard/try/ask", output={"ok": True}, after=timedelta(seconds=3)),
    ]
    log = run_case(Case("journal", RETRY_SCENARIO % "constant", script=script, input={"in": 1}))
    journal = [(s.node, s.visit, s.attempt, s.status) for s in build_journal(log.observations)]
    assert journal == [
        ("/guard", 1, 1, "retrying"),
        ("/guard/try/ask", 1, 1, "failed"),
        ("/guard", 1, 2, "completed"),
        ("/guard/try/ask", 2, 1, "completed"),
    ]
    first_try, failed_call, second_try, call = build_journal(log.observations)
    assert failed_call.problem.title == "Connection reset"
    assert (failed_call.started_at, failed_call.finished_at) == (T0, T0 + timedelta(seconds=2))
    assert first_try.problem == failed_call.problem
    assert (second_try.input, call.output) == ({"in": 1}, {"ok": True})
    assert (call.started_at, call.finished_at) == (T0 + timedelta(seconds=7), T0 + timedelta(seconds=10))


def test_journal_marks_skipped_steps():
    log = run_case(Case("skips", SEQUENCE, script=[Act(action=SkipTask(node="/ask", output=1))]))
    ask = next(s for s in build_journal(log.observations) if s.node == "/ask")
    assert (ask.status, ask.skipped_by, ask.output) == ("completed", "control", {"asked": 1})


def test_waits_are_visible_from_observations_and_state():
    log = run_case(Case("waits", SEQUENCE, script=[Finish(node="/ask", output=1)]))
    [wait] = current_waits(log.observations)
    assert (wait.node, wait.wait, wait.until) == ("/rest", "timer", T0 + timedelta(minutes=1))
    assert project(log.state).waits == (wait,)


def test_projection_for_the_api():
    log = run_case(Case("view", SEQUENCE, script=[Act(action=Pause()), Finish(node="/ask", output=1)]))
    view = project(log.state)
    assert (view.status, view.current_nodes, view.queued_stimuli, view.scenario_ref) == ("paused", ("/ask",), 1, "test/view:1.0.0")
    [wait] = view.waits
    assert (wait.node, wait.wait, wait.detail) == ("/ask", "invocation", {"capability": "echo:1@test"})


def test_projection_shows_the_last_error_and_failed_node():
    log = run_case(Case("broken", SEQUENCE, script=[Finish(node="/ask", error=COMMUNICATION)]))
    view = project(log.state)
    assert (view.status, view.failed_node, view.last_error.title, view.current_nodes) == ("failed", "/ask", "Connection reset", ("/ask",))
