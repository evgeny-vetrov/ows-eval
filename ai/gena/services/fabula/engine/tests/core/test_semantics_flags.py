"""Semantics flags are pinned at start: an engine with new defaults replays old fabulas the old way."""

import pytest

from ai.gena.services.fabula.engine.core.interpreter import Interpreter
from ai.gena.services.fabula.engine.core.semantics import DEFAULT_FLAGS, LISTEN_ALL_ORDER, validate_flags
from ai.gena.services.fabula.engine.core.state import FabulaState
from ai.gena.services.fabula.engine.model.context import ExecutionContext
from ai.gena.services.fabula.engine.model.stimuli import EventDelivered
from ai.gena.services.fabula.engine.testing.kit import Kit, scenario_text
from ai.gena.services.fabula.engine.testing.runner import T0, event

KIT = Kit()
BOTH = KIT.snapshot(
    scenario_text(
        """\
        do:
          - both:
              listen:
                to:
                  all:
                    - {with: {type: vcs.pr.merged}}
                    - {with: {type: vcs.pr.closed}}
        """
    )
)


def interpreter(flags=None) -> Interpreter:
    return Interpreter(expressions=KIT.expressions, matcher=KIT.matcher, schemas=KIT.schemas, new_fabula_semantics=flags)


def deliver(state: FabulaState, engine: Interpreter, index: int, ev, number: int) -> FabulaState:
    sub = f"f-1/both@1.1/sub.{index}"
    return engine.apply(state, EventDelivered(stimulus_id=f"s{number}", at=T0, subscription_id=sub, delivery_key=f"k{number}", event=ev)).state


def test_running_fabula_keeps_the_flags_it_started_with():
    old_engine = interpreter({LISTEN_ALL_ORDER: "arrival"})
    state = old_engine.start(BOTH, None, ExecutionContext(), T0, fabula_id="f-1").state
    assert state.semantics.flags == {LISTEN_ALL_ORDER: "arrival"}
    # The engine is upgraded: new fabulas get "declaration", the running one keeps "arrival".
    new_engine = interpreter()
    state = FabulaState.model_validate_json(state.model_dump_json())
    state = deliver(state, new_engine, 1, event("vcs.pr.closed", {"pr_id": "c"}, id="c"), 1)
    state = deliver(state, new_engine, 0, event("vcs.pr.merged", {"pr_id": "m"}, id="m"), 2)
    assert state.output == [{"pr_id": "c"}, {"pr_id": "m"}]
    fresh = new_engine.start(BOTH, None, ExecutionContext(), T0, fabula_id="f-1").state
    assert fresh.semantics.flags == DEFAULT_FLAGS


def test_state_without_a_flag_means_the_legacy_behaviour():
    state = interpreter().start(BOTH, None, ExecutionContext(), T0, fabula_id="f-1").state
    legacy = state.model_copy(update={"semantics": state.semantics.model_copy(update={"flags": {}})})
    assert legacy.semantics.flag(LISTEN_ALL_ORDER) == "arrival"


@pytest.mark.parametrize("flags", [{"unknown": "x"}, {LISTEN_ALL_ORDER: "random"}])
def test_unknown_flags_are_rejected(flags):
    with pytest.raises(ValueError):
        validate_flags(flags)
