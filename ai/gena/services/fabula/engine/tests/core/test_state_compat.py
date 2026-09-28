from datetime import timedelta

import pytest
from state_v1_fixture import STATE_V1

from ai.gena.services.fabula.engine.core.codec import MIGRATIONS, load_state
from ai.gena.services.fabula.engine.core.interpreter import InvalidState
from ai.gena.services.fabula.engine.core.projection import project
from ai.gena.services.fabula.engine.testing.pilot import pilot_kit
from ai.gena.services.fabula.engine.testing.runner import Deliver, Finish, Fire, ScenarioRunner, event
from ai.gena.services.fabula.engine.version import STATE_SCHEMA_VERSION


def test_frozen_v1_state_is_read_and_finished_by_the_current_engine():
    state = load_state(STATE_V1)
    assert (state.schema_version, state.status) == (1, "waiting")
    assert [(w.node, w.wait) for w in project(state).waits] == [
        ("/", "deadline"),
        ("/awaitMerge", "deadline"),
        ("/awaitMerge", "event"),
    ]
    script = [
        Deliver(node="/awaitMerge", event=event("vcs.pr.merged", {"pr_id": "PR-T-42", "repo": "gena"}), stimulus_id="merged"),
        Fire(node="/soak", role="wait", stimulus_id="soaked"),
        Finish(node="/healthCheck/try/probe", output={"status": "ok"}, stimulus_id="probed"),
        Finish(node="/approve", output={"approved": True}, stimulus_id="approved"),
        Finish(node="/comment", output={"comment_id": "C-1"}, stimulus_id="commented"),
    ]
    log = ScenarioRunner(pilot_kit()).run(state.scenario, script, state=state)
    assert log.state.status == "completed"
    assert log.state.output == {"ticket": "T-42", "pr": "PR-T-42", "approved": True}
    assert log.state.last_at - state.started_at == timedelta(days=3)


@pytest.mark.parametrize("document", ['{"schema_version": 99}', "{}", '{"schema_version": 0}'])
def test_unknown_schema_versions_are_refused(document):
    with pytest.raises(InvalidState):
        load_state(document)


def test_every_older_schema_has_a_migration():
    assert set(MIGRATIONS) == set(range(1, STATE_SCHEMA_VERSION))
