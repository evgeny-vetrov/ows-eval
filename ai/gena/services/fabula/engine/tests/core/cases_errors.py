"""FR-6: try / catch / retry."""

from ai.gena.services.fabula.engine.model.problem import ErrorType, Problem
from ai.gena.services.fabula.engine.testing.runner import Case, Finish, Fire

COMMUNICATION = Problem.of(ErrorType.COMMUNICATION, "Connection reset")
UNAVAILABLE = Problem(type=ErrorType.COMMUNICATION, status=503, title="Unavailable")
BAD_GATEWAY = Problem(type=ErrorType.COMMUNICATION, status=502, title="Bad gateway")

RAISE_VALIDATION = """
        - check:
            raise:
              error: {type: 'https://serverlessworkflow.io/spec/1.0.0/errors/validation', status: 400, title: Bad ticket}
"""

RETRY_SCENARIO = """
do:
  - guard:
      try:
        - ask: {call: 'echo:1@test'}
      catch:
        errors: {with: {type: 'https://serverlessworkflow.io/spec/1.0.0/errors/communication'}}
        retry:
          delay: PT5S
          backoff: {%s: {}}
          limit: {attempt: {count: 3}}
"""


def retry_case(backoff: str, fire_offsets: list[str]) -> Case:
    script = []
    for _ in range(3):
        script += [Finish(node="/guard/try/ask", error=COMMUNICATION), Fire(node="/guard", role="retry")]
    script.append(Finish(node="/guard/try/ask", output={"ok": True}))
    commands = []
    for attempt, fire_at in enumerate(fire_offsets, start=1):
        commands += [f"invoke guard/try/ask@{attempt}.1/call echo:1@test", f"timer guard@1.{attempt}/retry {fire_at}"]
    commands += ["invoke guard/try/ask@4.1/call echo:1@test", "completed"]
    return Case(f"retry_backoff_{backoff}", RETRY_SCENARIO % backoff, script=script, commands=commands, output={"ok": True})


ERROR_CASES = [
    Case(
        "catch_matching_error_runs_catch_do",
        """
do:
  - guard:
      try:
"""
        + RAISE_VALIDATION
        + """
      catch:
        errors: {with: {type: 'https://serverlessworkflow.io/spec/1.0.0/errors/validation', status: 400}}
        as: problem
        do:
          - report: {set: {title: '${ $problem.title }', status: '${ $problem.status }', where: '${ $problem.instance }'}}
""",
        observations=[
            "start /guard #1.1",
            "start /guard/try/check #1.1",
            "fail /guard/try/check validation",
            "start /guard/catch/report #1.1",
            "done /guard/catch/report",
            "done /guard",
            "fabula completed",
        ],
        output={"title": "Bad ticket", "status": 400, "where": "/do/0/guard/try/0/check"},
    ),
    Case(
        "catch_mismatch_propagates",
        """
do:
  - guard:
      try:
"""
        + RAISE_VALIDATION
        + """
      catch:
        errors: {with: {status: 500}}
        do:
          - never: {set: {}}
""",
        observations=["start /guard #1.1", "start /guard/try/check #1.1", "fail /guard/try/check validation", "fail /guard validation", "fabula failed"],
        status="failed",
    ),
    Case(
        "catch_when_and_except_when",
        """
do:
  - guard:
      try:
        - ask: {call: 'echo:1@test'}
      catch:
        when: '${ $error.status >= 500 }'
        exceptWhen: '${ $error.status == 502 }'
""",
        script=[Finish(node="/guard/try/ask", error=UNAVAILABLE)],
        input={"kept": True},
        status="completed",
        output={"kept": True},
    ),
    Case(
        "catch_except_when_propagates",
        """
do:
  - guard:
      try:
        - ask: {call: 'echo:1@test'}
      catch:
        when: '${ $error.status >= 500 }'
        exceptWhen: '${ $error.status == 502 }'
""",
        script=[Finish(node="/guard/try/ask", error=BAD_GATEWAY)],
        status="failed",
    ),
    Case(
        "nested_try_outer_catches",
        """
do:
  - outer:
      try:
        - inner:
            try:
              - check:
                  raise:
                    error: {type: 'https://serverlessworkflow.io/spec/1.0.0/errors/validation', status: 400}
            catch:
              errors: {with: {status: 500}}
      catch:
        do:
          - handled: {set: {by: outer}}
""",
        observations=[
            "start /outer #1.1",
            "start /outer/try/inner #1.1",
            "start /outer/try/inner/try/check #1.1",
            "fail /outer/try/inner/try/check validation",
            "fail /outer/try/inner validation",
            "start /outer/catch/handled #1.1",
            "done /outer/catch/handled",
            "done /outer",
            "fabula completed",
        ],
        output={"by": "outer"},
    ),
    Case(
        "error_in_catch_do_propagates",
        """
do:
  - guard:
      try:
"""
        + RAISE_VALIDATION
        + """
      catch:
        do:
          - again:
              raise:
                error: {type: 'https://serverlessworkflow.io/spec/1.0.0/errors/runtime', status: 500}
""",
        observations=[
            "start /guard #1.1",
            "start /guard/try/check #1.1",
            "fail /guard/try/check validation",
            "start /guard/catch/again #1.1",
            "fail /guard/catch/again runtime",
            "fail /guard runtime",
            "fabula failed",
        ],
        status="failed",
    ),
    retry_case("constant", ["+PT5S", "+PT10S", "+PT15S"]),
    retry_case("linear", ["+PT5S", "+PT15S", "+PT30S"]),
    retry_case("exponential", ["+PT5S", "+PT15S", "+PT35S"]),
    Case(
        "retry_new_attempt_reenters_children_with_new_visits",
        RETRY_SCENARIO % "constant",
        script=[Finish(node="/guard/try/ask", error=COMMUNICATION), Fire(node="/guard", role="retry"), Finish(node="/guard/try/ask", output=1)],
        observations=[
            "start /guard #1.1",
            "start /guard/try/ask #1.1",
            "fail /guard/try/ask communication",
            "retry /guard -> 2 at +PT5S",
            "start /guard #1.2",
            "start /guard/try/ask #2.1",
            "done /guard/try/ask",
            "done /guard",
            "fabula completed",
        ],
        output=1,
    ),
    Case(
        "retry_exhausted_without_catch_do_rethrows",
        """
do:
  - guard:
      try:
        - ask: {call: 'echo:1@test'}
      catch:
        retry: {delay: PT1S, limit: {attempt: {count: 1}}}
""",
        script=[Finish(node="/guard/try/ask", error=COMMUNICATION), Fire(node="/guard", role="retry"), Finish(node="/guard/try/ask", error=COMMUNICATION)],
        commands=["invoke guard/try/ask@1.1/call echo:1@test", "timer guard@1.1/retry +PT1S", "invoke guard/try/ask@2.1/call echo:1@test", "failed communication"],
        status="failed",
    ),
    Case(
        "retry_exhausted_runs_catch_do",
        """
do:
  - guard:
      try:
        - ask: {call: 'echo:1@test'}
      catch:
        retry: {delay: PT1S, limit: {attempt: {count: 1}}}
        do:
          - fallback: {set: {fallback: true}}
""",
        script=[Finish(node="/guard/try/ask", error=COMMUNICATION), Fire(node="/guard", role="retry"), Finish(node="/guard/try/ask", error=COMMUNICATION)],
        output={"fallback": True},
    ),
    Case(
        "retry_total_duration_limit",
        """
do:
  - guard:
      try:
        - ask: {call: 'echo:1@test'}
      catch:
        retry: {delay: PT10M, limit: {duration: PT15M}}
""",
        script=[Finish(node="/guard/try/ask", error=COMMUNICATION), Fire(node="/guard", role="retry"), Finish(node="/guard/try/ask", error=COMMUNICATION)],
        commands=["invoke guard/try/ask@1.1/call echo:1@test", "timer guard@1.1/retry +PT10M", "invoke guard/try/ask@2.1/call echo:1@test", "failed communication"],
        status="failed",
    ),
    Case(
        "retry_attempt_duration_times_out_an_attempt",
        """
do:
  - guard:
      try:
        - ask: {call: 'echo:1@test'}
      catch:
        retry: {delay: PT1S, limit: {attempt: {count: 1, duration: PT1M}}}
""",
        script=[Fire(node="/guard", role="attempt_timeout"), Fire(node="/guard", role="retry"), Finish(node="/guard/try/ask", output=1)],
        commands=[
            "timer guard@1.1/attempt-timeout +PT1M",
            "invoke guard/try/ask@1.1/call echo:1@test",
            "cancel-invocation guard/try/ask@1.1/call",
            "timer guard@1.1/retry +PT1M1S",
            "timer guard@1.2/attempt-timeout +PT2M1S",
            "invoke guard/try/ask@2.1/call echo:1@test",
            "cancel-timer guard@1.2/attempt-timeout",
            "completed",
        ],
        output=1,
    ),
    Case(
        "retry_when_filters_retryable_errors",
        """
do:
  - guard:
      try:
        - ask: {call: 'echo:1@test'}
      catch:
        retry:
          when: '${ $error.status == 503 }'
          delay: PT1S
          limit: {attempt: {count: 5}}
""",
        script=[Finish(node="/guard/try/ask", error=UNAVAILABLE), Fire(node="/guard", role="retry"), Finish(node="/guard/try/ask", error=BAD_GATEWAY)],
        commands=["invoke guard/try/ask@1.1/call echo:1@test", "timer guard@1.1/retry +PT1S", "invoke guard/try/ask@2.1/call echo:1@test", "failed communication"],
        status="failed",
    ),
]
