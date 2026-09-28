"""FR-10: interventions."""

from datetime import timedelta

from ai.gena.services.fabula.engine.core.limits import EngineLimits
from ai.gena.services.fabula.engine.model.problem import ErrorType, Problem
from ai.gena.services.fabula.engine.model.stimuli import (
    Cancel,
    CompleteWait,
    ExtendDeadline,
    Goto,
    PatchContext,
    Pause,
    Resume,
    RetryTask,
    SkipTask,
    Value,
)
from ai.gena.services.fabula.engine.testing.runner import T0, Act, Case, Deliver, Finish, Fire, event

DOWN = Problem.of(ErrorType.COMMUNICATION, "down")

SEQUENCE = """
do:
  - ask: {call: 'echo:1@test', output: {as: '${ {asked: .} }'}}
  - rest: {wait: PT1M}
  - last: {call: 'echo:1@test'}
"""

PARALLEL = """
do:
  - par:
      fork:
        branches:
          - left: {call: 'echo:1@test'}
          - right: {wait: PT1H}
  - after: {set: {after: true}}
"""

LOOP = """
do:
  - loop:
      for: {each: n, in: '${ .items }'}
      do:
        - work: {call: 'echo:1@test', with: {n: '${ $n }'}}
  - after: {set: {after: true}}
"""

NESTED = """
do:
  - first: {call: 'echo:1@test'}
  - group:
      do:
        - inner1: {set: {inner: 1}}
        - inner2: {set: {inner: 2}}
  - last: {set: {last: true}}
"""

LISTEN = """
do:
  - merged:
      listen:
        to:
          one: {with: {type: vcs.pr.merged}}
      timeout: {after: P1D}
"""

CONTROL_CASES = [
    Case(
        "pause_accumulates_and_resume_applies_in_order",
        SEQUENCE,
        script=[
            Act(action=Pause()),
            Finish(node="/ask", output=1, after=timedelta(minutes=1)),
            Act(action=Resume(), after=timedelta(minutes=10)),
            Fire(node="/rest", role="wait"),
        ],
        commands=["invoke ask@1.1/call echo:1@test", "timer rest@1.1/wait +PT12M", "invoke last@1.1/call echo:1@test"],
        observations=[
            "start /ask #1.1",
            "accepted pause",
            "queued invocation_finished",
            "accepted resume",
            "done /ask",
            "start /rest #1.1",
            "done /rest",
            "start /last #1.1",
        ],
        status="waiting",
    ),
    Case(
        "pause_backlog_is_bounded_and_redelivery_recovers",
        PARALLEL,
        limits=EngineLimits(max_paused_stimuli=1),
        script=[
            Act(action=Pause()),
            Finish(node="/par/left", output="left"),
            Fire(node="/par/right", role="wait", stimulus_id="fired:right"),
            Act(action=Resume()),
            # The producer redelivers with the same id: the rejected stimulus was not remembered.
            Fire(node="/par/right", role="wait", stimulus_id="fired:right"),
        ],
        observations=[
            "start /par #1.1",
            "start /par/left #1.1",
            "start /par/right #1.1",
            "accepted pause",
            "queued invocation_finished",
            "ignored timer_fired (paused_backlog_full)",
            "accepted resume",
            "done /par/left",
            "done /par/right",
            "done /par",
            "start /after #1.1",
            "done /after",
            "fabula completed",
        ],
        output={"after": True},
    ),
    Case(
        "pause_and_resume_are_checked_against_the_status",
        SEQUENCE,
        script=[Act(action=Resume()), Act(action=Pause()), Act(action=Pause())],
        observations=["start /ask #1.1", "rejected resume (invalid_status)", "accepted pause", "rejected pause (invalid_status)"],
        status="paused",
    ),
    Case(
        "repeated_control_request_is_ignored",
        SEQUENCE,
        script=[Act(action=Pause(), request_id="r1"), Act(action=Pause(), request_id="r1"), Act(action=Resume(), request_id="r2"), Act(action=Resume(), request_id="r2")],
        observations=["start /ask #1.1", "accepted pause", "ignored control (duplicate)", "accepted resume", "ignored control (duplicate)"],
        status="waiting",
    ),
    Case(
        "cancel_closes_everything",
        """
do:
  - par:
      fork:
        branches:
          - a: {call: 'echo:1@test'}
          - b: {wait: PT1H}
          - c:
              listen:
                to:
                  one: {with: {type: test.ping}}
""",
        script=[Act(action=Cancel()), Fire(op_id="f-1/par/b@1.1/wait"), Act(action=Resume())],
        commands=[
            "invoke par/a@1.1/call echo:1@test",
            "timer par/b@1.1/wait +PT1H",
            "subscribe par/c@1.1/sub.0 test.ping",
            "cancel-invocation par/a@1.1/call",
            "cancel-timer par/b@1.1/wait",
            "unsubscribe par/c@1.1/sub.0",
            "cancelled",
        ],
        observations=[
            "start /par #1.1",
            "start /par/a #1.1",
            "start /par/b #1.1",
            "start /par/c #1.1",
            "accepted cancel",
            "cancel /par",
            "cancel /par/a",
            "cancel /par/b",
            "cancel /par/c",
            "fabula cancelled",
            "ignored timer_fired (final)",
            "rejected resume (final)",
        ],
        status="cancelled",
    ),
    Case(
        "skip_task_cancels_its_operation_and_continues",
        SEQUENCE,
        script=[Act(action=SkipTask(node="/ask", output="manual")), Finish(op_id="f-1/ask@1.1/call", output="late")],
        commands=["invoke ask@1.1/call echo:1@test", "cancel-invocation ask@1.1/call", "timer rest@1.1/wait +PT1M"],
        observations=[
            "start /ask #1.1",
            "accepted skip_task",
            "skip /ask (control)",
            "done /ask",
            "start /rest #1.1",
            "ignored invocation_finished (late)",
        ],
        status="waiting",
    ),
    Case(
        "retry_task_reissues_the_current_call",
        SEQUENCE,
        script=[Act(action=RetryTask(node="/ask")), Finish(node="/ask", output=1)],
        commands=["invoke ask@1.1/call echo:1@test", "cancel-invocation ask@1.1/call", "invoke ask@1.2/call echo:1@test", "timer rest@1.1/wait +PT1M"],
        observations=["start /ask #1.1", "accepted retry_task", "cancel /ask", "start /ask #1.2", "done /ask", "start /rest #1.1"],
        status="waiting",
    ),
    Case(
        "retry_task_recovers_a_failed_fabula",
        """
timeout: {after: P1D}
do:
  - ask: {call: 'echo:1@test'}
""",
        script=[Finish(node="/ask", error=DOWN), Act(action=RetryTask(node="/ask")), Finish(node="/ask", output="ok")],
        commands=[
            "timer @workflow/timeout +P1D",
            "invoke ask@1.1/call echo:1@test",
            "cancel-timer @workflow/timeout",
            "failed communication",
            "timer @workflow/timeout~2 +P1D",
            "invoke ask@1.2/call echo:1@test",
            "cancel-timer @workflow/timeout~2",
            "completed",
        ],
        observations=["start /ask #1.1", "fail /ask communication", "fabula failed", "accepted retry_task", "start /ask #1.2", "done /ask", "fabula completed"],
        output="ok",
    ),
    Case(
        "retry_failed_branch_restarts_cancelled_siblings",
        PARALLEL,
        script=[
            Finish(node="/par/left", error=DOWN),
            Act(action=RetryTask(node="/par/left")),
            Finish(node="/par/left", output="left"),
            Fire(node="/par/right", role="wait"),
        ],
        commands=[
            "invoke par/left@1.1/call echo:1@test",
            "timer par/right@1.1/wait +PT1H",
            "cancel-timer par/right@1.1/wait",
            "failed communication",
            "timer par/right@2.1/wait +PT1H",
            "invoke par/left@1.2/call echo:1@test",
            "completed",
        ],
        output={"after": True},
    ),
    Case(
        "skip_failed_task_continues_the_fabula",
        SEQUENCE,
        script=[Finish(node="/ask", error=DOWN), Act(action=SkipTask(node="/ask", output="fixed"))],
        observations=["start /ask #1.1", "fail /ask communication", "fabula failed", "accepted skip_task", "skip /ask (control)", "done /ask", "start /rest #1.1"],
        status="waiting",
    ),
    Case(
        "skip_rejects_inactive_and_unknown_nodes",
        SEQUENCE,
        script=[Act(action=SkipTask(node="/rest")), Act(action=SkipTask(node="/nope")), Act(action=SkipTask(node="/"))],
        observations=["start /ask #1.1", "rejected skip_task (not_active)", "rejected skip_task (unknown_node)", "rejected skip_task (root_node)"],
        status="waiting",
    ),
    Case(
        "goto_same_scope_forward",
        SEQUENCE,
        script=[Act(action=Goto(node="/last"))],
        commands=["invoke ask@1.1/call echo:1@test", "cancel-invocation ask@1.1/call", "invoke last@1.1/call echo:1@test"],
        observations=["start /ask #1.1", "accepted goto", "cancel /ask", "start /last #1.1"],
        status="waiting",
    ),
    Case(
        "goto_same_scope_backward_with_input",
        SEQUENCE,
        script=[Finish(node="/ask", output=1), Act(action=Goto(node="/ask", input=Value(value={"again": True})))],
        commands=["invoke ask@1.1/call echo:1@test", "timer rest@1.1/wait +PT1M", "cancel-timer rest@1.1/wait", "invoke ask@2.1/call echo:1@test"],
        status="waiting",
    ),
    Case(
        "goto_out_of_fork_needs_permission",
        PARALLEL,
        script=[Act(action=Goto(node="/after")), Act(action=Goto(node="/after", allow_scope_change=True))],
        commands=[
            "invoke par/left@1.1/call echo:1@test",
            "timer par/right@1.1/wait +PT1H",
            "cancel-invocation par/left@1.1/call",
            "cancel-timer par/right@1.1/wait",
            "completed",
        ],
        observations=[
            "start /par #1.1",
            "start /par/left #1.1",
            "start /par/right #1.1",
            "rejected goto (cross_scope)",
            "accepted goto",
            "cancel /par",
            "cancel /par/left",
            "cancel /par/right",
            "start /after #1.1",
            "done /after",
            "fabula completed",
        ],
        output={"after": True},
    ),
    Case(
        "goto_out_of_loop_needs_permission",
        LOOP,
        input={"items": [1, 2, 3]},
        script=[Finish(node="/loop/work", output=1), Act(action=Goto(node="/after")), Act(action=Goto(node="/after", allow_scope_change=True))],
        commands=["invoke loop/work@1.1/call echo:1@test", "invoke loop/work@2.1/call echo:1@test", "cancel-invocation loop/work@2.1/call", "completed"],
        observations=[
            "start /loop #1.1",
            "start /loop/work #1.1",
            "done /loop/work",
            "start /loop/work #2.1",
            "rejected goto (cross_scope)",
            "accepted goto",
            "cancel /loop",
            "cancel /loop/work",
            "start /after #1.1",
            "done /after",
            "fabula completed",
        ],
        output={"after": True},
    ),
    Case(
        "goto_into_loop_body_is_rejected",
        NESTED.replace("  - group:\n      do:", "  - group:\n      for: {in: '${ [1] }'}\n      do:"),
        script=[Act(action=Goto(node="/group/inner2", allow_scope_change=True))],
        observations=["start /first #1.1", "rejected goto (not_constructible)"],
        status="waiting",
    ),
    Case(
        "goto_into_group_with_permission",
        NESTED,
        script=[Act(action=Goto(node="/group/inner2")), Act(action=Goto(node="/group/inner2", allow_scope_change=True))],
        observations=[
            "start /first #1.1",
            "rejected goto (cross_scope)",
            "accepted goto",
            "cancel /first",
            "start /group #1.1",
            "start /group/inner2 #1.1",
            "done /group/inner2",
            "done /group",
            "start /last #1.1",
            "done /last",
            "fabula completed",
        ],
        output={"last": True},
    ),
    Case(
        "goto_inside_branch_keeps_other_branches",
        """
do:
  - par:
      fork:
        branches:
          - left:
              do:
                - l1: {call: 'echo:1@test'}
                - l2: {set: {left: done}}
          - right: {wait: PT1H}
""",
        script=[Act(action=Goto(node="/par/left/l2")), Fire(node="/par/right", role="wait")],
        commands=["timer par/right@1.1/wait +PT1H", "invoke par/left/l1@1.1/call echo:1@test", "cancel-invocation par/left/l1@1.1/call", "completed"],
        output=[{"left": "done"}, None],
    ),
    Case(
        "goto_cannot_target_a_branch",
        PARALLEL,
        script=[Act(action=Goto(node="/par/right", allow_scope_change=True))],
        observations=["start /par #1.1", "start /par/left #1.1", "start /par/right #1.1", "rejected goto (fork_branch)"],
        status="waiting",
    ),
    Case(
        "goto_while_paused_enters_after_resume",
        SEQUENCE,
        script=[Act(action=Pause()), Act(action=Goto(node="/last")), Act(action=Resume())],
        commands=["invoke ask@1.1/call echo:1@test", "cancel-invocation ask@1.1/call", "invoke last@1.1/call echo:1@test"],
        observations=["start /ask #1.1", "accepted pause", "accepted goto", "cancel /ask", "accepted resume", "start /last #1.1"],
        status="waiting",
    ),
    Case(
        "patch_context_is_seen_by_later_tasks",
        """
do:
  - rest: {wait: PT1M}
  - read: {set: {ticket: '${ $context.ticket }', gone: '${ $context.old }'}}
""",
        script=[Act(action=PatchContext(patch={"ticket": "T-9", "old": None})), Fire(node="/rest", role="wait")],
        output={"ticket": "T-9", "gone": None},
        data={"ticket": "T-9"},
    ),
    Case(
        "complete_wait_listen_with_event",
        LISTEN,
        script=[Act(action=CompleteWait(node="/merged", event=event("vcs.pr.merged", {"pr_id": "manual"})))],
        commands=["timer merged@1.1/timeout +P1D", "subscribe merged@1.1/sub.0 vcs.pr.merged", "cancel-timer merged@1.1/timeout", "unsubscribe merged@1.1/sub.0", "completed"],
        output=[{"pr_id": "manual"}],
    ),
    Case(
        "complete_wait_on_wait_task_and_rejects_other_tasks",
        SEQUENCE,
        script=[
            Act(action=CompleteWait(node="/ask", output=Value(value=1))),
            Finish(node="/ask", output=1),
            Act(action=CompleteWait(node="/rest", output=Value(value={"manual": True}))),
        ],
        commands=["invoke ask@1.1/call echo:1@test", "timer rest@1.1/wait +PT1M", "cancel-timer rest@1.1/wait", "invoke last@1.1/call echo:1@test"],
        observations=["start /ask #1.1", "rejected complete_wait (not_a_wait)", "done /ask", "start /rest #1.1", "accepted complete_wait", "done /rest", "start /last #1.1"],
        status="waiting",
    ),
    Case(
        "extend_deadline_moves_the_wait_and_ignores_the_old_timer",
        SEQUENCE,
        script=[
            Finish(node="/ask", output=1),
            Act(action=ExtendDeadline(node="/rest", fire_at=T0 + timedelta(hours=2))),
            Fire(op_id="f-1/rest@1.1/wait"),
            Act(action=ExtendDeadline(node="/rest", fire_at=T0 - timedelta(hours=1))),
            Fire(node="/rest", role="wait"),
        ],
        commands=[
            "invoke ask@1.1/call echo:1@test",
            "timer rest@1.1/wait +PT1M",
            "cancel-timer rest@1.1/wait",
            "timer rest@1.1/wait~2 +PT2H",
            "invoke last@1.1/call echo:1@test",
        ],
        observations=[
            "start /ask #1.1",
            "done /ask",
            "start /rest #1.1",
            "accepted extend_deadline",
            "ignored timer_fired (late)",
            "rejected extend_deadline (in_past)",
            "done /rest",
            "start /last #1.1",
        ],
        status="waiting",
    ),
    Case(
        "extend_deadline_of_listen_renews_the_subscription_expiry",
        LISTEN,
        script=[Act(action=ExtendDeadline(node="/merged", fire_at=T0 + timedelta(days=5)))],
        commands=[
            "timer merged@1.1/timeout +P1D",
            "subscribe merged@1.1/sub.0 vcs.pr.merged",
            "cancel-timer merged@1.1/timeout",
            "timer merged@1.1/timeout~2 +P5D",
            "subscribe merged@1.1/sub.0 vcs.pr.merged",
        ],
        status="waiting",
    ),
    Case(
        "extend_workflow_deadline",
        """
timeout: {after: PT1H}
do:
  - rest: {wait: PT2H}
""",
        script=[Act(action=ExtendDeadline(node="/", fire_at=T0 + timedelta(hours=3))), Fire(node="/rest", role="wait")],
        commands=["timer @workflow/timeout +PT1H", "timer rest@1.1/wait +PT2H", "cancel-timer @workflow/timeout", "timer @workflow/timeout~2 +PT3H", "cancel-timer @workflow/timeout~2", "completed"],
        status="completed",
    ),
    Case(
        "listen_after_delivery_during_pause",
        LISTEN,
        script=[Act(action=Pause()), Deliver(node="/merged", event=event("vcs.pr.merged", {"pr_id": "1"})), Act(action=Resume())],
        observations=["start /merged #1.1", "accepted pause", "queued event_delivered", "accepted resume", "done /merged", "fabula completed"],
        output=[{"pr_id": "1"}],
    ),
]
