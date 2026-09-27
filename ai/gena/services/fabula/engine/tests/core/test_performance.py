import time

from ai.gena.services.fabula.engine.core.interpreter import Interpreter
from ai.gena.services.fabula.engine.model.context import ExecutionContext
from ai.gena.services.fabula.engine.model.stimuli import InvocationFinished, OutcomeOk
from ai.gena.services.fabula.engine.testing.kit import Kit, scenario_text
from ai.gena.services.fabula.engine.testing.runner import T0


def test_a_transition_takes_milliseconds():
    kit = Kit()
    steps = "".join(f"  - step{i}: {{call: 'echo:1@test', with: {{n: {i}, prev: '${{ . }}'}}}}\n" for i in range(100))
    snapshot = kit.snapshot(scenario_text("do:\n" + steps))
    interpreter = Interpreter(expressions=kit.expressions, matcher=kit.matcher, schemas=kit.schemas)
    state = interpreter.start(snapshot, {"seed": 1}, ExecutionContext(), T0, fabula_id="f").state
    durations = []
    for i in range(100):
        stimulus = InvocationFinished(stimulus_id=f"s{i}", at=T0, invocation_id=f"f/step{i}@1.1/call", outcome=OutcomeOk(output={"i": i}))
        started = time.perf_counter()
        state = interpreter.apply(state, stimulus).state
        durations.append(time.perf_counter() - started)
    assert state.status == "completed"
    durations.sort()
    assert durations[len(durations) // 2] < 0.005, f"median transition {durations[50] * 1000:.2f} ms"
    assert durations[-1] < 0.05
