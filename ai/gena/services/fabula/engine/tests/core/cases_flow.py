"""FR-2: sequence, data flow, if, switch, then, set, raise."""

from ai.gena.services.fabula.engine.testing.runner import Case

FLOW_CASES = [
    Case(
        "dataflow_chain",
        """
input:
  from: '${ {n: .number} }'
do:
  - double:
      input: {from: '${ {v: .n} }'}
      set: {v: '${ .v * 2 }', raw: '${ $task.input.n }'}
      output: {as: '${ {doubled: .v, raw: .raw} }'}
      export: {as: '${ $context + {last: .doubled} }'}
  - describe:
      set:
        text: '${ "got " + (.doubled | tostring) }'
        task: '${ $task.name }'
        number: '${ $workflow.input.number }'
        id: '${ $workflow.id }'
        last: '${ $context.last }'
output:
  as: '${ . + {runtime: $runtime.name} }'
""",
        input={"number": 21},
        commands=["completed"],
        observations=["start /double #1.1", "done /double", "start /describe #1.1", "done /describe", "fabula completed"],
        status="completed",
        output={"text": "got 42", "task": "describe", "number": 21, "id": "f-1", "last": 42, "runtime": "fabula"},
        data={"last": 42},
    ),
    Case(
        "if_false_skips_and_passes_input_through",
        """
do:
  - maybe:
      if: '${ .run }'
      set: {ran: true}
      then: end
  - after:
      set: {seen: '${ . }'}
""",
        input={"run": False},
        observations=["skip /maybe (condition)", "start /after #1.1", "done /after", "fabula completed"],
        output={"seen": {"run": False}},
    ),
    Case(
        "if_true_runs_task",
        """
do:
  - maybe:
      if: '${ .run }'
      set: {ran: true}
""",
        input={"run": True},
        output={"ran": True},
    ),
    *[
        Case(
            f"switch_routes_{label}",
            """
do:
  - route:
      switch:
        - low: {when: '${ .v < 10 }', then: small}
        - high: {when: '${ .v >= 10 }', then: big}
  - small:
      set: {size: small}
      then: end
  - big:
      set: {size: big}
""",
            input={"v": value},
            observations=["start /route #1.1", "done /route", f"start /{label} #1.1", f"done /{label}", "fabula completed"],
            output={"size": label},
        )
        for label, value in (("small", 3), ("big", 30))
    ],
    Case(
        "switch_default_and_fallthrough",
        """
do:
  - route:
      switch:
        - never: {when: '${ false }', then: end}
        - other: {then: tail}
  - skipped: {set: {skipped: true}}
  - tail: {set: {tail: true}}
""",
        observations=["start /route #1.1", "done /route", "start /tail #1.1", "done /tail", "fabula completed"],
        output={"tail": True},
    ),
    Case(
        "then_exit_leaves_the_group",
        """
do:
  - group:
      do:
        - first: {set: {step: 1}, then: exit}
        - never: {set: {step: 2}}
  - after: {set: {after: '${ .step }'}}
""",
        observations=["start /group #1.1", "start /group/first #1.1", "done /group/first", "done /group", "start /after #1.1", "done /after", "fabula completed"],
        output={"after": 1},
    ),
    Case(
        "then_end_from_nested_group",
        """
do:
  - group:
      do:
        - first: {set: {step: 1}, then: end}
        - never: {set: {step: 2}}
  - after: {set: {after: true}}
output:
  as: '${ {final: .step} }'
""",
        observations=["start /group #1.1", "start /group/first #1.1", "done /group/first", "cancel /group", "fabula completed"],
        output={"final": 1},
    ),
    Case(
        "then_end_inside_a_branch_closes_other_branches",
        """
do:
  - par:
      fork:
        branches:
          - quick:
              do:
                - stop: {set: {stopped: true}, then: end}
          - slow: {wait: PT1H}
  - never: {set: {}}
""",
        commands=["timer par/slow@1.1/wait +PT1H", "cancel-timer par/slow@1.1/wait", "completed"],
        observations=[
            "start /par #1.1",
            "start /par/quick #1.1",
            "start /par/slow #1.1",
            "start /par/quick/stop #1.1",
            "done /par/quick/stop",
            "cancel /par",
            "cancel /par/quick",
            "cancel /par/slow",
            "fabula completed",
        ],
        output={"stopped": True},
    ),
    Case(
        "then_jumps_backwards_with_new_visits",
        """
do:
  - init: {set: {}, export: {as: '${ {i: 0} }'}}
  - inc:
      set: {}
      export: {as: '${ {i: ($context.i + 1)} }'}
  - check:
      switch:
        - again: {when: '${ $context.i < 3 }', then: inc}
        - done: {then: continue}
  - finish: {set: {i: '${ $context.i }'}}
""",
        observations=[
            "start /init #1.1", "done /init",
            "start /inc #1.1", "done /inc", "start /check #1.1", "done /check",
            "start /inc #2.1", "done /inc", "start /check #2.1", "done /check",
            "start /inc #3.1", "done /inc", "start /check #3.1", "done /check",
            "start /finish #1.1", "done /finish", "fabula completed",
        ],
        output={"i": 3},
        data={"i": 3},
    ),
    Case(
        "raise_unhandled_fails_the_fabula",
        """
do:
  - boom:
      raise:
        error:
          type: https://example.com/errors/boom
          status: 409
          title: Boom
          detail: '${ "value " + (.v | tostring) }'
  - never: {set: {}}
""",
        input={"v": 7},
        commands=["failed https://example.com/errors/boom"],
        observations=["start /boom #1.1", "fail /boom https://example.com/errors/boom", "fabula failed"],
        status="failed",
    ),
    Case(
        "raise_referenced_error",
        """
use:
  errors:
    notFound: {type: 'https://serverlessworkflow.io/spec/1.0.0/errors/runtime', status: 404, title: Not found}
do:
  - boom:
      raise: {error: notFound}
""",
        commands=["failed runtime"],
        status="failed",
    ),
    Case(
        "expression_error_fails_the_task",
        """
do:
  - bad: {set: {x: '${ .a.b }'}}
""",
        input={"a": 5},
        commands=["failed expression"],
        observations=["start /bad #1.1", "fail /bad expression", "fabula failed"],
        status="failed",
    ),
    Case(
        "workflow_input_schema_rejects_input",
        """
input:
  schema:
    document: {type: object, required: [ticket]}
do:
  - a: {set: {}}
""",
        input={"other": 1},
        commands=["failed validation"],
        observations=["fabula failed"],
        status="failed",
    ),
    Case(
        "workflow_output_schema_rejects_output",
        """
do:
  - a: {set: {n: 1}}
output:
  schema:
    document: {type: object, properties: {n: {type: string}}}
""",
        commands=["failed validation"],
        status="failed",
    ),
    Case(
        "task_input_and_output_schemas",
        """
do:
  - a:
      input: {schema: {document: {type: object, required: [n]}}}
      set: {n: '${ .n }', m: 1}
      output: {schema: {document: {type: object, required: [m]}}}
""",
        input={"n": 2},
        output={"n": 2, "m": 1},
    ),
    Case(
        "task_input_schema_violation_fails",
        """
do:
  - a:
      input: {schema: {document: {type: object, required: [n]}}}
      set: {}
""",
        input={},
        observations=["start /a #1.1", "fail /a validation", "fabula failed"],
        status="failed",
    ),
    Case(
        "export_as_replaces_context",
        """
do:
  - a:
      set: {v: 1}
      export: {as: {first: '${ .v }'}}
  - b:
      set: {v: 2}
      export: {as: '${ $context + {second: .v} }'}
""",
        data={"first": 1, "second": 2},
    ),
]
