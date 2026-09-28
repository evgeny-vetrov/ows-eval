"""FR-10: version swap."""

from ai.gena.services.fabula.engine.model.stimuli import Goto, Pause, SwapVersion
from ai.gena.services.fabula.engine.testing.kit import Kit, scenario_text
from ai.gena.services.fabula.engine.testing.runner import Act, Case, Deliver, Finish, event

KIT = Kit()

V1 = """
do:
  - prepare: {set: {}, export: {as: '${ {ticket: $input.ticket} }'}}
  - ask: {call: 'echo:1@test', with: {ticket: '${ $context.ticket }'}}
  - merged:
      listen:
        to:
          one: {with: {type: vcs.pr.merged}}
  - finish: {set: {version: 1}}
"""

# Same waits, different tail: every in-flight operation survives.
V2_COMPATIBLE = """
do:
  - prepare: {set: {}, export: {as: '${ {ticket: $input.ticket} }'}}
  - ask: {call: 'echo:1@test', with: {ticket: '${ $context.ticket }'}}
  - merged:
      listen:
        to:
          one: {with: {type: vcs.pr.merged}}
  - finish: {set: {version: 2, ticket: '${ $context.ticket }'}}
"""

# The wait is renamed; the listen itself is unchanged.
V2_RENAMED = V2_COMPATIBLE.replace("  - merged:", "  - prMerged:")

# The call moves to another capability and the listen filters change.
V2_INCOMPATIBLE = """
do:
  - prepare: {set: {}, export: {as: '${ {ticket: $input.ticket} }'}}
  - ask: {call: 'strict:1@test', with: {x: 1}}
  - merged:
      listen:
        to:
          one: {with: {type: vcs.pr.merged, data: {repo: gena}}}
  - finish: {set: {version: 3, owner: '${ $context.owner }', ticket: '${ $context.ticket }'}}
"""

SNAPSHOT_V1 = KIT.snapshot(scenario_text(V1, name="swap", version="1.0.0"))
SNAPSHOT_V2 = KIT.snapshot(scenario_text(V2_COMPATIBLE, name="swap", version="2.0.0"))
SNAPSHOT_V2_RENAMED = KIT.snapshot(scenario_text(V2_RENAMED, name="swap", version="2.1.0"))
SNAPSHOT_V3 = KIT.snapshot(scenario_text(V2_INCOMPATIBLE, name="swap", version="3.0.0"))

MERGED = event("vcs.pr.merged", {"pr_id": "PR-1", "repo": "gena"})
V1_TEXT = scenario_text(V1, name="swap", version="1.0.0")

SWAP_CASES = [
    Case(
        "swap_compatible_during_invocation",
        V1_TEXT,
        input={"ticket": "T-1"},
        script=[Act(action=SwapVersion(scenario=SNAPSHOT_V2)), Finish(node="/ask", output={}), Deliver(node="/merged", event=MERGED)],
        commands=["invoke ask@1.1/call echo:1@test", "subscribe merged@1.1/sub.0 vcs.pr.merged", "unsubscribe merged@1.1/sub.0", "completed"],
        observations=[
            "start /prepare #1.1",
            "done /prepare",
            "start /ask #1.1",
            "accepted swap_version",
            "swapped test/swap:1.0.0 -> test/swap:2.0.0",
            "done /ask",
            "start /merged #1.1",
            "done /merged",
            "start /finish #1.1",
            "done /finish",
            "fabula completed",
        ],
        output={"version": 2, "ticket": "T-1"},
    ),
    Case(
        "swap_compatible_during_event_wait_with_rename",
        V1_TEXT,
        input={"ticket": "T-1"},
        script=[
            Finish(node="/ask", output={}),
            Act(action=SwapVersion(scenario=SNAPSHOT_V2_RENAMED, mapping={"/merged": "/prMerged"})),
            Deliver(node="/prMerged", event=MERGED),
        ],
        commands=["invoke ask@1.1/call echo:1@test", "subscribe merged@1.1/sub.0 vcs.pr.merged", "unsubscribe merged@1.1/sub.0", "completed"],
        output={"version": 2, "ticket": "T-1"},
    ),
    Case(
        "swap_incompatible_without_mapping_is_rejected",
        V1_TEXT,
        input={"ticket": "T-1"},
        script=[Act(action=SwapVersion(scenario=SNAPSHOT_V3)), Finish(node="/ask", output={})],
        commands=["invoke ask@1.1/call echo:1@test", "subscribe merged@1.1/sub.0 vcs.pr.merged"],
        observations=["start /prepare #1.1", "done /prepare", "start /ask #1.1", "rejected swap_version (incompatible)", "done /ask", "start /merged #1.1"],
        status="waiting",
    ),
    Case(
        "swap_incompatible_with_mapping_and_migration",
        V1_TEXT,
        input={"ticket": "T-1"},
        script=[
            Finish(node="/ask", output={}),
            Act(
                action=SwapVersion(
                    scenario=SNAPSHOT_V3,
                    mapping={"/merged": "/merged"},
                    context_migration='${ . + {owner: "team-" + .ticket} }',
                )
            ),
            Deliver(node="/merged", event=MERGED),
        ],
        commands=[
            "invoke ask@1.1/call echo:1@test",
            "subscribe merged@1.1/sub.0 vcs.pr.merged",
            "unsubscribe merged@1.1/sub.0",
            "subscribe merged@2.1/sub.0 vcs.pr.merged",
            "unsubscribe merged@2.1/sub.0",
            "completed",
        ],
        observations=[
            "start /prepare #1.1",
            "done /prepare",
            "start /ask #1.1",
            "done /ask",
            "start /merged #1.1",
            "accepted swap_version",
            "cancel /merged",
            "swapped test/swap:1.0.0 -> test/swap:3.0.0",
            "start /merged #2.1",
            "done /merged",
            "start /finish #1.1",
            "done /finish",
            "fabula completed",
        ],
        output={"version": 3, "owner": "team-T-1", "ticket": "T-1"},
        data={"ticket": "T-1", "owner": "team-T-1"},
    ),
    Case(
        "swap_restarts_invocation_on_new_capability",
        V1_TEXT,
        input={"ticket": "T-1"},
        script=[Act(action=SwapVersion(scenario=SNAPSHOT_V3, mapping={"/ask": "/ask"})), Finish(node="/ask", output={"y": 1})],
        commands=[
            "invoke ask@1.1/call echo:1@test",
            "cancel-invocation ask@1.1/call",
            "invoke ask@2.1/call strict:1@test",
            "subscribe merged@1.1/sub.0 vcs.pr.merged",
        ],
        status="waiting",
    ),
    Case(
        "swap_restart_target_must_stay_in_scope",
        V1_TEXT,
        input={"ticket": "T-1"},
        script=[Act(action=SwapVersion(scenario=SNAPSHOT_V3, mapping={"/ask": "/nowhere"}))],
        observations=["start /prepare #1.1", "done /prepare", "start /ask #1.1", "rejected swap_version (incompatible)"],
        status="waiting",
    ),
    Case(
        "swap_is_rejected_with_pending_work",
        V1_TEXT,
        input={"ticket": "T-1"},
        script=[Act(action=Pause()), Act(action=Goto(node="/finish")), Act(action=SwapVersion(scenario=SNAPSHOT_V2))],
        observations=["start /prepare #1.1", "done /prepare", "start /ask #1.1", "accepted pause", "accepted goto", "cancel /ask", "rejected swap_version (not_at_rest)"],
        status="paused",
    ),
]
