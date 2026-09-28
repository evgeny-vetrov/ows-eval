from ai.gena.services.fabula.engine.core.limits import EngineLimits
from ai.gena.services.fabula.engine.testing.kit import Kit
from ai.gena.services.fabula.engine.testing.runner import Case, Deliver, Finish, ScenarioRunner, case_text, event, run_case

NESTED = """
do:
  - outer:
      try:
        - loop:
            for: {each: n, in: '${ .items }'}
            do:
              - par:
                  fork:
                    branches:
                      - ask: {call: 'echo:1@test', with: {n: '${ $n }'}}
                      - hold: {wait: PT1H}
      catch:
        do:
          - fallback: {set: {}}
"""


def test_cursor_is_an_explicit_frame_stack():
    log = run_case(Case("stack", NESTED, input={"items": ["a", "b"]}))
    frames = log.state.frames
    assert list(frames) == ["/", "/outer", "/outer/try/loop", "/outer/try/loop/par", "/outer/try/loop/par/ask", "/outer/try/loop/par/hold"]
    assert frames["/outer"].phase == "try" and frames["/outer"].current == "/outer/try/loop"
    assert (frames["/outer/try/loop"].index, frames["/outer/try/loop"].current) == (0, "/outer/try/loop/par")
    assert [b.status for b in frames["/outer/try/loop/par"].branches] == ["running", "running"]
    assert frames["/outer/try/loop/par/ask"].invocation_id == "f-1/outer/try/loop/par/ask@1.1/call"


STREAM = """
do:
  - stream:
      listen:
        to:
          any: [{with: {type: test.ping}}]
          until: '${ .last == true }'
      foreach:
        do:
          - count: {set: {}, export: {as: '${ {seen: (($context.seen // 0) + 1)} }'}}
"""


def test_long_event_stream_keeps_the_state_bounded():
    kit = Kit()
    runner = ScenarioRunner(kit, limits=EngineLimits(max_delivery_keys=16, max_recent_stimuli=16))
    snapshot = kit.snapshot(case_text(Case("stream", STREAM)))
    state = runner.run(snapshot, []).state
    sizes = []
    for n in range(1, 301):
        step = Deliver(node="/stream", event=event("test.ping", {"n": n, "last": n == 300}, id=f"p-{n}"))
        log = runner.run(snapshot, [step], state=state)
        state = log.state
        sizes.append(len(log.transitions[-1].state_json))
    assert state.status == "completed" and state.data == {"seen": 300}
    # Nothing accumulates: from event 116 on, the 16-entry dedup windows hold only
    # three-digit ids, like every counter, so the persisted state keeps exactly one size.
    assert len(set(sizes[115:299])) == 1


def test_state_carries_versions_and_restores_from_json():
    log = run_case(Case("versions", NESTED, input={"items": ["a"]}, script=[Finish(node="/outer/try/loop/par/ask", output=1)]))
    state = log.state
    assert (state.schema_version, state.engine_version, state.semantics.engine_version) == (1, "0.1.0", "0.1.0")
    assert state.scenario.schema_version == 1
    assert type(state).model_validate_json(state.model_dump_json()) == state
