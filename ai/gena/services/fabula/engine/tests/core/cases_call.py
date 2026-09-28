"""FR-3: capability calls."""

from datetime import timedelta

from ai.gena.services.fabula.engine.model.problem import ErrorType, Problem
from ai.gena.services.fabula.engine.testing.runner import Case, Finish, Fire, Raw
from ai.gena.services.fabula.engine.model.stimuli import InvocationFinished, OutcomeOk

COMMUNICATION = Problem.of(ErrorType.COMMUNICATION, "Connection reset")
AUTHORIZATION = Problem.of(ErrorType.AUTHORIZATION, "Forbidden")


def late_result(op_id: str, output=None):
    return Raw(build=lambda at, sid: InvocationFinished(stimulus_id=sid, at=at, invocation_id=op_id, outcome=OutcomeOk(output=output)))


CALL_CASES = [
    Case(
        "call_invokes_and_completes",
        """
do:
  - ask:
      call: 'echo:1@test'
      with: {question: '${ .q }', fixed: 1}
      output: {as: '${ {answer: .text} }'}
""",
        input={"q": "why"},
        script=[Finish(node="/ask", output={"text": "because"}, after=timedelta(minutes=5))],
        commands=["invoke ask@1.1/call echo:1@test", "completed"],
        observations=["start /ask #1.1", "done /ask", "fabula completed"],
        status="completed",
        output={"answer": "because"},
    ),
    Case(
        "call_input_violates_schema",
        """
do:
  - ask:
      call: 'strict:1@test'
      with: {x: '${ .x }'}
""",
        input={"x": "not a number"},
        commands=["failed validation"],
        observations=["start /ask #1.1", "fail /ask validation", "fabula failed"],
        status="failed",
    ),
    Case(
        "call_output_violates_schema",
        """
do:
  - ask:
      call: 'strict:1@test'
      with: {x: 1}
""",
        script=[Finish(node="/ask", output={"y": "text"})],
        commands=["invoke ask@1.1/call strict:1@test", "failed validation"],
        status="failed",
    ),
    Case(
        "call_timeout_cancels_and_ignores_late_result",
        """
do:
  - ask:
      call: 'slow:1@test'
""",
        script=[Fire(node="/ask", role="timeout"), late_result("f-1/ask@1.1/call")],
        commands=["timer ask@1.1/timeout +PT1H", "invoke ask@1.1/call slow:1@test", "cancel-invocation ask@1.1/call", "failed timeout"],
        observations=["start /ask #1.1", "fail /ask timeout", "fabula failed", "ignored invocation_finished (late)"],
        status="failed",
    ),
    Case(
        "call_timeout_is_catchable",
        """
do:
  - guard:
      try:
        - ask:
            call: 'echo:1@test'
            timeout: {after: PT30S}
      catch:
        errors: {with: {type: 'https://serverlessworkflow.io/spec/1.0.0/errors/timeout'}}
        do:
          - fallback: {set: {answer: default}}
""",
        script=[Fire(node="/guard/try/ask", role="timeout"), late_result("f-1/guard/try/ask@1.1/call")],
        commands=[
            "timer guard/try/ask@1.1/timeout +PT30S",
            "invoke guard/try/ask@1.1/call echo:1@test",
            "cancel-invocation guard/try/ask@1.1/call",
            "completed",
        ],
        observations=[
            "start /guard #1.1",
            "start /guard/try/ask #1.1",
            "fail /guard/try/ask timeout",
            "start /guard/catch/fallback #1.1",
            "done /guard/catch/fallback",
            "done /guard",
            "fabula completed",
            "ignored invocation_finished (final)",
        ],
        output={"answer": "default"},
    ),
    Case(
        "call_not_cancellable_is_abandoned_on_timeout",
        """
do:
  - fire:
      call: 'fire:1@test'
""",
        script=[Fire(node="/fire", role="timeout"), late_result("f-1/fire@1.1/call")],
        commands=["timer fire@1.1/timeout +PT5M", "invoke fire@1.1/call fire:1@test", "failed timeout"],
        observations=["start /fire #1.1", "fail /fire timeout", "fabula failed", "ignored invocation_finished (late)"],
        status="failed",
    ),
    Case(
        "call_default_retries_with_new_attempts",
        """
do:
  - flaky:
      call: 'flaky:1@test'
""",
        script=[
            Finish(node="/flaky", error=COMMUNICATION),
            Fire(node="/flaky", role="retry"),
            Finish(node="/flaky", error=COMMUNICATION),
            Fire(node="/flaky", role="retry"),
            Finish(node="/flaky", output={"ok": True}),
        ],
        commands=[
            "invoke flaky@1.1/call flaky:1@test",
            "timer flaky@1.1/retry +PT10S",
            "invoke flaky@1.2/call flaky:1@test",
            "timer flaky@1.2/retry +PT30S",
            "invoke flaky@1.3/call flaky:1@test",
            "completed",
        ],
        observations=[
            "start /flaky #1.1",
            "retry /flaky -> 2 at +PT10S",
            "start /flaky #1.2",
            "retry /flaky -> 3 at +PT30S",
            "start /flaky #1.3",
            "done /flaky",
            "fabula completed",
        ],
        output={"ok": True},
    ),
    Case(
        "call_default_retry_exhausted",
        """
do:
  - flaky: {call: 'flaky:1@test'}
""",
        script=[
            Finish(node="/flaky", error=COMMUNICATION),
            Fire(node="/flaky", role="retry"),
            Finish(node="/flaky", error=COMMUNICATION),
            Fire(node="/flaky", role="retry"),
            Finish(node="/flaky", error=COMMUNICATION),
        ],
        status="failed",
    ),
    Case(
        "call_default_retry_skips_other_errors",
        """
do:
  - flaky: {call: 'flaky:1@test'}
""",
        script=[Finish(node="/flaky", error=AUTHORIZATION)],
        commands=["invoke flaky@1.1/call flaky:1@test", "failed authorization"],
        status="failed",
    ),
    Case(
        "call_cancelled_by_executor",
        """
do:
  - ask: {call: 'echo:1@test'}
""",
        script=[Finish(node="/ask", cancelled=True)],
        commands=["invoke ask@1.1/call echo:1@test", "failed runtime"],
        status="failed",
    ),
    Case(
        "call_result_delivered_twice",
        """
do:
  - ask: {call: 'echo:1@test'}
  - next: {call: 'echo:1@test'}
""",
        script=[
            Finish(node="/ask", output=1, stimulus_id="r1"),
            Finish(op_id="f-1/ask@1.1/call", output=1, stimulus_id="r1"),
            Finish(op_id="f-1/ask@1.1/call", output=2, stimulus_id="r1-redelivered"),
        ],
        commands=["invoke ask@1.1/call echo:1@test", "invoke next@1.1/call echo:1@test"],
        observations=["start /ask #1.1", "done /ask", "start /next #1.1", "ignored invocation_finished (duplicate)", "ignored invocation_finished (late)"],
        status="waiting",
    ),
]
