"""FR-8: fork."""

from ai.gena.services.fabula.engine.model.problem import ErrorType, Problem
from ai.gena.services.fabula.engine.testing.runner import Case, Deliver, Finish, Fire, event

THREE_WAY = """
do:
  - race:
      fork:
        compete: %s
        branches:
          - sleeper: {wait: PT1H}
          - asker:
              do:
                - ask: {call: 'echo:1@test', with: {who: asker}}
          - listener:
              listen:
                to:
                  one: {with: {type: test.ping}}
"""

FORK_CASES = [
    Case(
        "fork_collects_outputs_in_declaration_order",
        THREE_WAY % "false",
        script=[
            Deliver(node="/race/listener", event=event("test.ping", {"n": 1})),
            Finish(node="/race/asker/ask", output="asked"),
            Fire(node="/race/sleeper", role="wait"),
        ],
        commands=[
            "timer race/sleeper@1.1/wait +PT1H",
            "subscribe race/listener@1.1/sub.0 test.ping",
            "invoke race/asker/ask@1.1/call echo:1@test",
            "unsubscribe race/listener@1.1/sub.0",
            "completed",
        ],
        output=[None, "asked", [{"n": 1}]],
    ),
    Case(
        "fork_compete_first_wins_and_losers_are_closed",
        THREE_WAY % "true",
        script=[Finish(node="/race/asker/ask", output="asked"), Fire(op_id="f-1/race/sleeper@1.1/wait")],
        commands=[
            "timer race/sleeper@1.1/wait +PT1H",
            "subscribe race/listener@1.1/sub.0 test.ping",
            "invoke race/asker/ask@1.1/call echo:1@test",
            "cancel-timer race/sleeper@1.1/wait",
            "unsubscribe race/listener@1.1/sub.0",
            "completed",
        ],
        observations=[
            "start /race #1.1",
            "start /race/sleeper #1.1",
            "start /race/asker #1.1",
            "start /race/listener #1.1",
            "start /race/asker/ask #1.1",
            "done /race/asker/ask",
            "done /race/asker",
            "cancel /race/sleeper",
            "cancel /race/listener",
            "done /race",
            "fabula completed",
            "ignored timer_fired (final)",
        ],
        output="asked",
    ),
    Case(
        "fork_compete_cancels_in_flight_invocation",
        THREE_WAY % "true",
        script=[Deliver(node="/race/listener", event=event("test.ping", {"n": 1}))],
        commands=[
            "timer race/sleeper@1.1/wait +PT1H",
            "subscribe race/listener@1.1/sub.0 test.ping",
            "invoke race/asker/ask@1.1/call echo:1@test",
            "unsubscribe race/listener@1.1/sub.0",
            "cancel-timer race/sleeper@1.1/wait",
            "cancel-invocation race/asker/ask@1.1/call",
            "completed",
        ],
        output=[{"n": 1}],
    ),
    Case(
        "fork_compete_synchronous_branch_wins_before_others_start",
        """
do:
  - race:
      fork:
        compete: true
        branches:
          - quick: {set: {winner: quick}}
          - slow: {wait: PT1H}
""",
        commands=["completed"],
        observations=["start /race #1.1", "start /race/quick #1.1", "done /race/quick", "done /race", "fabula completed"],
        output={"winner": "quick"},
    ),
    Case(
        "fork_branch_failure_cancels_siblings",
        THREE_WAY % "false",
        script=[Finish(node="/race/asker/ask", error=Problem.of(ErrorType.COMMUNICATION, "down"))],
        commands=[
            "timer race/sleeper@1.1/wait +PT1H",
            "subscribe race/listener@1.1/sub.0 test.ping",
            "invoke race/asker/ask@1.1/call echo:1@test",
            "cancel-timer race/sleeper@1.1/wait",
            "unsubscribe race/listener@1.1/sub.0",
            "failed communication",
        ],
        observations=[
            "start /race #1.1",
            "start /race/sleeper #1.1",
            "start /race/asker #1.1",
            "start /race/listener #1.1",
            "start /race/asker/ask #1.1",
            "fail /race/asker/ask communication",
            "fail /race/asker communication",
            "cancel /race/sleeper",
            "cancel /race/listener",
            "fail /race communication",
            "fabula failed",
        ],
        status="failed",
    ),
    Case(
        "fork_failure_is_catchable",
        """
do:
  - guard:
      try:
        - par:
            fork:
              branches:
                - bad:
                    raise:
                      error: {type: 'https://serverlessworkflow.io/spec/1.0.0/errors/runtime', status: 500}
                - good: {wait: PT1M}
      catch:
        do:
          - recover: {set: {recovered: true}}
""",
        # `bad` fails synchronously, so `good` never starts.
        commands=["completed"],
        output={"recovered": True},
    ),
]
