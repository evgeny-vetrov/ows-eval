from ai.gena.services.fabula.engine.dsl.loader import compile_snapshot
from ai.gena.services.fabula.engine.model.catalog import CapabilityDescriptor
from ai.gena.services.fabula.engine.testing.kit import Kit, scenario_text

KIT = Kit()

TREE = scenario_text(
    """\
    do:
      - prepare: {set: {items: [1, 2]}}
      - guard:
          try:
            - work: {call: 'echo:1@test'}
          catch:
            as: problem
            do:
              - report: {set: {failed: '${ $problem.status }'}}
      - loop:
          for: {in: '${ .items }', each: n}
          do:
            - step: {set: {v: '${ $n }'}}
      - par:
          fork:
            compete: true
            branches:
              - left: {wait: PT1S}
              - right:
                  do:
                    - inner: {wait: PT2S}
      - stream:
          listen:
            to:
              any: [{with: {type: test.ping}}]
              until: '${ .data.last == true }'
          foreach:
            do:
              - handle: {set: {seen: '${ $item }'}}
    """
)


def test_node_ids_are_paths_by_names():
    graph = KIT.load(TREE).graph
    assert sorted(graph.nodes) == [
        "/",
        "/guard",
        "/guard/catch/report",
        "/guard/try/work",
        "/loop",
        "/loop/step",
        "/par",
        "/par/left",
        "/par/right",
        "/par/right/inner",
        "/prepare",
        "/stream",
        "/stream/handle",
    ]
    assert graph.node("/guard").children == {"try": ("/guard/try/work",), "catch": ("/guard/catch/report",)}
    assert graph.node("/par/right/inner").pointer == "/do/3/par/fork/branches/1/right/do/0/inner"
    assert graph.ancestors("/par/right/inner") == ["/", "/par", "/par/right"]


def test_node_ids_survive_insertions():
    before = KIT.load(TREE).graph
    after = KIT.load(TREE.replace("do:\n  - prepare:", "do:\n  - first: {set: {}}\n  - prepare:", 1)).graph
    assert set(before.nodes) <= set(after.nodes)
    assert before.node("/loop/step").pointer != after.node("/loop/step").pointer


def test_snapshot_is_deterministic_and_self_contained():
    first = KIT.snapshot(TREE)
    second = KIT.snapshot(TREE)
    assert first.digest == second.digest
    kit = Kit()
    kit.capabilities.add(CapabilityDescriptor(ref="echo:1@test", cancellable=False))
    assert kit.snapshot(TREE).digest != first.digest
    # Compiling a snapshot only needs the descriptors pinned in it.
    graph = compile_snapshot(first, expressions=KIT.expressions, schemas=KIT.schemas)
    assert graph.node("/guard/try/work").capability.cancellable is True


def test_compat_keys_capture_what_must_match_on_swap():
    graph = KIT.load(TREE).graph
    assert graph.node("/guard/try/work").compat_key == "call:echo:1@test"
    assert graph.node("/stream").compat_key.startswith("listen:")
    assert graph.node("/par").compat_key == "fork:True"


def test_context_references_are_collected():
    graph = KIT.load(
        scenario_text(
            """\
            do:
              - a: {set: {x: '${ $context.ticket.id }', y: '${ $context.pr }'}}
            """
        )
    ).graph
    assert graph.context_keys == ("ticket", "pr")
