"""FR-7: for / while."""

from ai.gena.services.fabula.engine.core.limits import EngineLimits
from ai.gena.services.fabula.engine.testing.runner import Case, Finish

LOOP_CASES = [
    Case(
        "for_each_at_folds_iterations",
        """
do:
  - loop:
      for: {each: n, in: '${ .items }', at: i}
      do:
        - add: {set: {items: '${ .items }', sum: '${ .sum + $n }', at: '${ $i }'}}
""",
        input={"items": [1, 2, 3], "sum": 0},
        observations=[
            "start /loop #1.1",
            "start /loop/add #1.1",
            "done /loop/add",
            "start /loop/add #2.1",
            "done /loop/add",
            "start /loop/add #3.1",
            "done /loop/add",
            "done /loop",
            "fabula completed",
        ],
        output={"items": [1, 2, 3], "sum": 6, "at": 2},
    ),
    Case(
        "for_while_stops_early",
        """
do:
  - loop:
      for: {each: n, in: '${ .items }'}
      while: '${ .sum < 3 }'
      do:
        - add: {set: {items: '${ .items }', sum: '${ .sum + $n }'}}
""",
        input={"items": [1, 2, 3, 4], "sum": 0},
        output={"items": [1, 2, 3, 4], "sum": 3},
    ),
    Case(
        "for_calls_get_one_invocation_per_iteration",
        """
do:
  - loop:
      for: {in: '${ .ids }'}
      do:
        - ask:
            call: 'echo:1@test'
            with: {id: '${ $item }', position: '${ $index }'}
""",
        input={"ids": ["a", "b"]},
        script=[Finish(node="/loop/ask", output={"r": 1}), Finish(node="/loop/ask", output={"r": 2})],
        commands=["invoke loop/ask@1.1/call echo:1@test", "invoke loop/ask@2.1/call echo:1@test", "completed"],
        output={"r": 2},
    ),
    Case(
        "for_exit_breaks_the_loop",
        """
do:
  - loop:
      for: {each: n, in: '${ .items }'}
      do:
        - track: {set: {items: '${ .items }', last: '${ $n }'}}
        - stop:
            switch:
              - found: {when: '${ $n == 2 }', then: exit}
""",
        input={"items": [1, 2, 3]},
        output={"items": [1, 2, 3], "last": 2},
    ),
    Case(
        "for_empty_collection_returns_input",
        """
do:
  - loop:
      for: {in: '${ .items }'}
      do:
        - never: {set: {}}
""",
        input={"items": []},
        observations=["start /loop #1.1", "done /loop", "fabula completed"],
        output={"items": []},
    ),
    Case(
        "for_collection_must_be_an_array",
        """
do:
  - loop:
      for: {in: '${ .items }'}
      do:
        - never: {set: {}}
""",
        input={"items": {"a": 1}},
        commands=["failed validation"],
        status="failed",
    ),
    Case(
        "for_iteration_ceiling",
        """
do:
  - loop:
      for: {in: '${ .items }'}
      do:
        - step: {set: {items: '${ .items }'}}
""",
        input={"items": [1, 2, 3]},
        limits=EngineLimits(max_for_iterations=2),
        observations=[
            "start /loop #1.1",
            "start /loop/step #1.1",
            "done /loop/step",
            "start /loop/step #2.1",
            "done /loop/step",
            "fail /loop runtime",
            "fabula failed",
        ],
        status="failed",
    ),
    Case(
        "nested_loops_see_both_variables",
        """
do:
  - rows:
      for: {each: r, in: '${ .rows }'}
      do:
        - cols:
            for: {each: c, in: '${ .cols }'}
            do:
              - cell:
                  set:
                    rows: '${ .rows }'
                    cols: '${ .cols }'
                    cells: '${ .cells + [$r * 10 + $c] }'
""",
        input={"rows": [1, 2], "cols": [1, 2], "cells": []},
        output={"rows": [1, 2], "cols": [1, 2], "cells": [11, 12, 21, 22]},
    ),
]
