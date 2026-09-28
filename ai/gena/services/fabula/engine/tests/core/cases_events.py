"""FR-5: listen and emit."""

from datetime import timedelta

from ai.gena.services.fabula.engine.testing.runner import Case, Deliver, Finish, Fire, event

MERGED_7 = event("vcs.pr.merged", {"pr_id": "PR-7", "repo": "gena"}, id="e-7")
MERGED_8 = event("vcs.pr.merged", {"pr_id": "PR-8", "repo": "gena"}, id="e-8")
CLOSED_7 = event("vcs.pr.closed", {"pr_id": "PR-7"}, id="c-7")

CORRELATED = """
do:
  - remember: {set: {}, export: {as: '${ {pr: $input.pr} }'}}
  - merged:
      listen:
        to:
          one:
            with: {type: vcs.pr.merged, source: test, data: {repo: gena}}
            correlate:
              pr: {from: '${ .data.pr_id }', expect: '${ $context.pr }'}
"""

PINGS = """
do:
  - stream:
      listen:
        to:
          any: [{with: {type: test.ping}}]
          until: '${ .last == true }'
"""


def ping(n: int, last: bool = False):
    return event("test.ping", {"n": n, "last": last}, id=f"p-{n}")


EVENT_CASES = [
    Case(
        "listen_one_with_correlation",
        CORRELATED,
        input={"pr": "PR-7"},
        script=[Deliver(node="/merged", event=MERGED_7, after=timedelta(hours=2))],
        commands=["subscribe merged@1.1/sub.0 vcs.pr.merged", "unsubscribe merged@1.1/sub.0", "completed"],
        observations=["start /remember #1.1", "done /remember", "start /merged #1.1", "done /merged", "fabula completed"],
        output=[{"pr_id": "PR-7", "repo": "gena"}],
    ),
    Case(
        "listen_rechecks_filter_of_broad_delivery",
        CORRELATED,
        input={"pr": "PR-7"},
        script=[Deliver(node="/merged", event=MERGED_8), Deliver(node="/merged", event=MERGED_7)],
        observations=[
            "start /remember #1.1",
            "done /remember",
            "start /merged #1.1",
            "ignored event_delivered (filter_mismatch)",
            "done /merged",
            "fabula completed",
        ],
        output=[{"pr_id": "PR-7", "repo": "gena"}],
    ),
    Case(
        "listen_ignores_duplicates_and_late_deliveries",
        PINGS,
        script=[
            Deliver(node="/stream", event=ping(1)),
            Deliver(node="/stream", event=ping(1)),
            Deliver(node="/stream", event=ping(2, last=True)),
            Deliver(op_id="f-1/stream@1.1/sub.0", event=ping(3), key="late"),
        ],
        commands=["subscribe stream@1.1/sub.0 test.ping", "unsubscribe stream@1.1/sub.0", "completed"],
        observations=[
            "start /stream #1.1",
            "ignored event_delivered (duplicate)",
            "done /stream",
            "fabula completed",
            "ignored event_delivered (final)",
        ],
        output=[{"n": 1, "last": False}, {"n": 2, "last": True}],
    ),
    Case(
        "listen_event_after_unsubscribe_while_running",
        """
do:
  - first:
      listen:
        to:
          one: {with: {type: test.ping}}
  - second:
      listen:
        to:
          one: {with: {type: test.stop}}
""",
        script=[Deliver(node="/first", event=ping(1)), Deliver(op_id="f-1/first@1.1/sub.0", event=ping(2), key="again")],
        observations=["start /first #1.1", "done /first", "start /second #1.1", "ignored event_delivered (late)"],
        status="waiting",
    ),
    Case(
        "listen_any_first_event_wins",
        """
do:
  - outcome:
      listen:
        to:
          any:
            - {with: {type: vcs.pr.merged}}
            - {with: {type: vcs.pr.closed}}
""",
        script=[Deliver(node="/outcome", index=1, event=CLOSED_7)],
        commands=[
            "subscribe outcome@1.1/sub.0 vcs.pr.merged",
            "subscribe outcome@1.1/sub.1 vcs.pr.closed",
            "unsubscribe outcome@1.1/sub.0",
            "unsubscribe outcome@1.1/sub.1",
            "completed",
        ],
        output=[{"pr_id": "PR-7"}],
    ),
    *[
        Case(
            f"listen_all_in_any_order_{order}",
            """
do:
  - both:
      listen:
        to:
          all:
            - {with: {type: vcs.pr.merged}}
            - {with: {type: vcs.pr.closed}}
""",
            script=[Deliver(node="/both", index=1, event=CLOSED_7), Deliver(node="/both", index=0, event=MERGED_7)],
            commands=[
                "subscribe both@1.1/sub.0 vcs.pr.merged",
                "subscribe both@1.1/sub.1 vcs.pr.closed",
                "unsubscribe both@1.1/sub.1",
                "unsubscribe both@1.1/sub.0",
                "completed",
            ],
            semantics={"listen_all_order": order},
            output=expected,
        )
        for order, expected in (
            ("declaration", [{"pr_id": "PR-7", "repo": "gena"}, {"pr_id": "PR-7"}]),
            ("arrival", [{"pr_id": "PR-7"}, {"pr_id": "PR-7", "repo": "gena"}]),
        )
    ],
    Case(
        "listen_until_condition",
        PINGS,
        script=[Deliver(node="/stream", event=ping(1)), Deliver(node="/stream", event=ping(2)), Deliver(node="/stream", event=ping(3, last=True))],
        output=[{"n": 1, "last": False}, {"n": 2, "last": False}, {"n": 3, "last": True}],
    ),
    Case(
        "listen_until_event",
        """
do:
  - stream:
      listen:
        to:
          any: [{with: {type: test.ping}}]
          until:
            one: {with: {type: test.stop}}
""",
        script=[Deliver(node="/stream", event=ping(1)), Deliver(node="/stream", role="until", event=event("test.stop", {}, id="stop"))],
        commands=[
            "subscribe stream@1.1/sub.0 test.ping",
            "subscribe stream@1.1/until.0 test.stop",
            "unsubscribe stream@1.1/until.0",
            "unsubscribe stream@1.1/sub.0",
            "completed",
        ],
        output=[{"n": 1, "last": False}],
    ),
    Case(
        "listen_foreach_processes_each_event_and_buffers",
        """
do:
  - stream:
      listen:
        to:
          any: [{with: {type: test.ping}}]
          until: '${ .last == true }'
      foreach:
        item: ev
        at: pos
        do:
          - handle:
              call: 'echo:1@test'
              with: {n: '${ $ev.n }', pos: '${ $pos }'}
        export: {as: '${ {handled: (($context.handled // []) + [$ev.n])} }'}
""",
        input={"seed": True},
        script=[
            Deliver(node="/stream", event=ping(1)),
            Deliver(node="/stream", event=ping(2)),
            Deliver(node="/stream", event=ping(3, last=True)),
            Finish(node="/stream/handle", output={"done": 1}),
            Finish(node="/stream/handle", output={"done": 2}),
            Finish(node="/stream/handle", output={"done": 3}),
        ],
        commands=[
            "subscribe stream@1.1/sub.0 test.ping",
            "invoke stream/handle@1.1/call echo:1@test",
            "invoke stream/handle@2.1/call echo:1@test",
            "invoke stream/handle@3.1/call echo:1@test",
            "unsubscribe stream@1.1/sub.0",
            "completed",
        ],
        data={"handled": [1, 2, 3]},
        output={"done": 3},
    ),
    Case(
        "listen_timeout_unsubscribes_and_is_catchable",
        """
do:
  - guard:
      try:
        - merged:
            listen:
              to:
                one: {with: {type: vcs.pr.merged}}
            timeout: {after: P7D}
      catch:
        errors: {with: {type: 'https://serverlessworkflow.io/spec/1.0.0/errors/timeout'}}
        do:
          - giveUp: {set: {merged: false}}
""",
        script=[Fire(node="/guard/try/merged", role="timeout"), Deliver(op_id="f-1/guard/try/merged@1.1/sub.0", event=MERGED_7)],
        commands=[
            "timer guard/try/merged@1.1/timeout +P7D",
            "subscribe guard/try/merged@1.1/sub.0 vcs.pr.merged",
            "unsubscribe guard/try/merged@1.1/sub.0",
            "completed",
        ],
        output={"merged": False},
    ),
    Case(
        "listen_read_envelope",
        """
do:
  - merged:
      listen:
        to:
          one: {with: {type: vcs.pr.merged}}
        read: envelope
      output: {as: '${ {id: .[0].id, pr: .[0].data.pr_id, tenant: .[0].attributes.tenant} }'}
""",
        script=[Deliver(node="/merged", event=event("vcs.pr.merged", {"pr_id": "PR-1"}, id="env-1", tenant="acme"))],
        output={"id": "env-1", "pr": "PR-1", "tenant": "acme"},
    ),
    Case(
        "emit_publishes_event",
        """
do:
  - notify:
      emit:
        event:
          with:
            type: test.notice
            subject: '${ .ticket }'
            data: {text: '${ "done " + .ticket }'}
            tenant: acme
""",
        input={"ticket": "T-1"},
        commands=["publish notify@1.1/emit test.notice", "completed"],
        observations=["start /notify #1.1", "done /notify", "fabula completed"],
        output={"ticket": "T-1"},
    ),
    Case(
        "emit_data_violates_schema",
        """
do:
  - notify:
      emit:
        event:
          with: {type: test.notice, data: {text: '${ .n }'}}
""",
        input={"n": 5},
        commands=["failed validation"],
        status="failed",
    ),
]
