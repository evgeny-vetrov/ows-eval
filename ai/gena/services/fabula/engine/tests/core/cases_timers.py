"""FR-4: wait, task deadlines, workflow deadline, schedule.after."""

from datetime import timedelta

from ai.gena.services.fabula.engine.testing.runner import Case, Finish, Fire

TIMER_CASES = [
    Case(
        "wait_duration",
        """
do:
  - pause: {wait: P3D}
  - after: {set: {woke: true}}
""",
        script=[Fire(node="/pause", role="wait")],
        commands=["timer pause@1.1/wait +P3D", "completed"],
        observations=["start /pause #1.1", "done /pause", "start /after #1.1", "done /after", "fabula completed"],
        output={"woke": True},
    ),
    Case(
        "wait_object_duration_passes_input_through",
        """
do:
  - pause: {wait: {hours: 1, minutes: 30}}
""",
        input={"keep": 1},
        script=[Fire(node="/pause", role="wait")],
        commands=["timer pause@1.1/wait +PT1H30M", "completed"],
        output={"keep": 1},
    ),
    Case(
        "wait_until_instant",
        """
do:
  - pause:
      wait: {until: '${ .deadline }'}
""",
        input={"deadline": "2026-01-05T12:00:00+03:00"},
        script=[Fire(node="/pause", role="wait")],
        commands=["timer pause@1.1/wait +P4DT9H", "completed"],
    ),
    Case(
        "wait_until_past_fires_now",
        """
do:
  - pause:
      wait: {until: '2025-12-01T00:00:00Z'}
""",
        script=[Fire(node="/pause", role="wait")],
        commands=["timer pause@1.1/wait +PT0S", "completed"],
    ),
    Case(
        "wait_dynamic_duration",
        """
do:
  - pause: {wait: '${ .delay }'}
""",
        input={"delay": "PT45M"},
        script=[Fire(node="/pause", role="wait")],
        commands=["timer pause@1.1/wait +PT45M", "completed"],
    ),
    Case(
        "wait_dynamic_duration_over_limit",
        """
do:
  - pause: {wait: '${ .delay }'}
""",
        input={"delay": "P200D"},
        commands=["failed validation"],
        status="failed",
    ),
    Case(
        "task_deadline_interrupts_wait",
        """
do:
  - pause:
      wait: PT2H
      timeout: {after: PT1H}
""",
        script=[Fire(node="/pause", role="timeout")],
        commands=["timer pause@1.1/timeout +PT1H", "timer pause@1.1/wait +PT2H", "cancel-timer pause@1.1/wait", "failed timeout"],
        observations=["start /pause #1.1", "fail /pause timeout", "fabula failed"],
        status="failed",
    ),
    Case(
        "deadline_is_cancelled_when_task_finishes",
        """
do:
  - pause:
      wait: PT1M
      timeout: {after: PT1H}
""",
        script=[Fire(node="/pause", role="wait")],
        commands=["timer pause@1.1/timeout +PT1H", "timer pause@1.1/wait +PT1M", "cancel-timer pause@1.1/timeout", "completed"],
    ),
    Case(
        "group_deadline_cancels_everything_inside",
        """
do:
  - group:
      timeout: {after: PT10M}
      do:
        - ask: {call: 'echo:1@test'}
        - pause: {wait: PT1H}
""",
        script=[Finish(node="/group/ask", output={}, after=timedelta(minutes=1)), Fire(node="/group", role="timeout")],
        commands=[
            "timer group@1.1/timeout +PT10M",
            "invoke group/ask@1.1/call echo:1@test",
            "timer group/pause@1.1/wait +PT1H1M",
            "cancel-timer group/pause@1.1/wait",
            "failed timeout",
        ],
        observations=["start /group #1.1", "start /group/ask #1.1", "done /group/ask", "start /group/pause #1.1", "cancel /group/pause", "fail /group timeout", "fabula failed"],
        status="failed",
    ),
    Case(
        "workflow_deadline",
        """
timeout: {after: P1D}
do:
  - ask: {call: 'echo:1@test'}
""",
        script=[Fire(node="/", role="workflow_timeout")],
        commands=["timer @workflow/timeout +P1D", "invoke ask@1.1/call echo:1@test", "cancel-invocation ask@1.1/call", "failed timeout"],
        observations=["start /ask #1.1", "cancel /ask", "fabula failed"],
        status="failed",
    ),
    Case(
        "workflow_deadline_cancelled_on_completion",
        """
timeout: {after: P1D}
do:
  - a: {set: {done: true}}
""",
        commands=["timer @workflow/timeout +P1D", "cancel-timer @workflow/timeout", "completed"],
        status="completed",
    ),
    Case(
        "schedule_after_delays_the_start",
        """
schedule: {after: PT15M}
timeout: {after: PT1H}
do:
  - a: {set: {done: true}}
""",
        script=[Fire(node="/", role="schedule")],
        commands=["timer @workflow/schedule +PT15M", "timer @workflow/timeout +PT1H15M", "cancel-timer @workflow/timeout", "completed"],
        observations=["start /a #1.1", "done /a", "fabula completed"],
        output={"done": True},
    ),
]
