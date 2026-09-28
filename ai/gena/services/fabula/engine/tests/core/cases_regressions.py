"""Regressions found by review; each case pins the corrected behaviour."""

from datetime import timedelta

from ai.gena.services.fabula.engine.core.limits import EngineLimits
from ai.gena.services.fabula.engine.model.problem import ErrorType, Problem
from ai.gena.services.fabula.engine.model.stimuli import Goto, PatchContext, RetryTask, SwapVersion
from ai.gena.services.fabula.engine.testing.kit import Kit, scenario_text
from ai.gena.services.fabula.engine.testing.runner import Act, Case, Finish, Fire

DOWN = Problem.of(ErrorType.COMMUNICATION, "down")
V2_STRICT = Kit().snapshot(
    scenario_text(
        """\
        do:
          - ask: {call: 'strict:1@test', with: {x: 1}}
          - fin: {set: {v: 2}}
        """,
        name="sw",
        version="2.0.0",
    )
)

REGRESSION_CASES = [
    Case(
        "goto_while_scheduled_starts_the_workflow_now",
        """
schedule: {after: PT15M}
timeout: {after: PT1H}
do:
  - first: {call: 'echo:1@test'}
  - second: {call: 'echo:1@test'}
""",
        script=[Act(action=Goto(node="/second")), Finish(node="/second", output={"s": 2})],
        commands=[
            "timer @workflow/schedule +PT15M",
            "cancel-timer @workflow/schedule",
            "timer @workflow/timeout +PT1H",
            "invoke second@1.1/call echo:1@test",
            "cancel-timer @workflow/timeout",
            "completed",
        ],
        output={"s": 2},
    ),
    Case(
        "recovered_deadline_takes_a_new_id_and_still_fires",
        """
do:
  - group:
      timeout: {after: PT10M}
      do:
        - ask: {call: 'echo:1@test'}
        - pause: {wait: PT1H}
""",
        script=[Fire(node="/group", role="timeout"), Act(action=Goto(node="/group/pause"), after=timedelta(hours=1)), Fire(node="/group", role="timeout")],
        commands=[
            "timer group@1.1/timeout +PT10M",
            "invoke group/ask@1.1/call echo:1@test",
            "cancel-invocation group/ask@1.1/call",
            "failed timeout",
            "timer group@1.1/timeout~2 +PT1H20M",
            "timer group/pause@1.1/wait +PT2H10M",
            "cancel-timer group/pause@1.1/wait",
            "failed timeout",
        ],
        status="failed",
    ),
    Case(
        "failing_scope_drops_its_pending_work",
        """
do:
  - t:
      try:
        - par:
            timeout: {after: PT1M}
            fork:
              branches:
                - a: {call: 'echo:1@test'}
                - b: {call: 'echo:1@test'}
                - c: {call: 'echo:1@test'}
      catch:
        errors: {with: {type: 'https://serverlessworkflow.io/spec/1.0.0/errors/timeout'}}
  - rest: {wait: PT1H}
""",
        limits=EngineLimits(max_steps_per_transition=3),
        script=[Fire(node="/t/try/par", role="timeout")],
        # The first transition yields with Enter(b), Enter(c) pending; the deadline drops them.
        commands=[
            "timer t/try/par@1.1/timeout +PT1M",
            "invoke t/try/par/a@1.1/call echo:1@test",
            "timer @workflow/yield.1 +PT0S",
            "cancel-invocation t/try/par/a@1.1/call",
            "timer rest@1.1/wait +PT1H1M",
        ],
        status="waiting",
    ),
    Case(
        "retrying_a_failed_branch_of_a_compete_fork",
        """
do:
  - par:
      fork:
        compete: true
        branches:
          - a: {call: 'strict:1@test', with: {x: '${ $context.n }'}}
          - b: {wait: PT1H}
  - after: {set: {after: true}}
""",
        script=[Act(action=PatchContext(patch={"n": 1})), Act(action=RetryTask(node="/par/a")), Finish(node="/par/a", output={"y": 1})],
        commands=[
            "failed validation",
            "timer par/b@1.1/wait +PT1H",
            "invoke par/a@1.2/call strict:1@test",
            "cancel-timer par/b@1.1/wait",
            "completed",
        ],
        output={"after": True},
    ),
    Case(
        "swap_with_restart_recovers_a_failed_fabula",
        scenario_text(
            """\
            do:
              - ask: {call: 'echo:1@test'}
              - fin: {set: {v: 1}}
            """,
            name="sw",
            version="1.0.0",
        ),
        script=[Finish(node="/ask", error=DOWN), Act(action=SwapVersion(scenario=V2_STRICT, mapping={"/ask": "/ask"})), Finish(node="/ask", output={"y": 1})],
        commands=["invoke ask@1.1/call echo:1@test", "failed communication", "invoke ask@2.1/call strict:1@test", "completed"],
        output={"v": 2},
    ),
]
