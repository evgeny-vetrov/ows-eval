from datetime import timedelta

import pytest

from cases_events import CORRELATED, EVENT_CASES

from ai.gena.services.fabula.engine.model.filter import AllOf, Predicate
from ai.gena.services.fabula.engine.testing.runner import T0, Case, check_case, run_case


@pytest.mark.parametrize("case", EVENT_CASES, ids=str)
def test_events(case: Case):
    check_case(case)


def test_subscribe_carries_filter_correlation_since_and_context():
    log = run_case(Case("subscribe", CORRELATED, input={"pr": "PR-7"}))
    [subscribe] = log.commands
    assert subscribe.subscription_id == "f-1/merged@1.1/sub.0"
    assert subscribe.event_types == ("vcs.pr.merged",)
    assert subscribe.filter == AllOf(
        filters=(
            Predicate(attr="source", op="eq", value="test"),
            Predicate(attr="data.repo", op="eq", value="gena"),
            Predicate(attr="data.pr_id", op="eq", value="PR-7"),
        )
    )
    assert subscribe.since == T0
    assert subscribe.expires_at is None
    assert subscribe.context.run_as == "tester"


def test_subscription_expires_after_the_nearest_deadline_plus_margin():
    scenario = """
timeout: {after: P30D}
do:
  - merged:
      listen:
        to:
          one: {with: {type: vcs.pr.merged}}
      timeout: {after: P7D}
"""
    log = run_case(Case("expiry", scenario))
    [subscribe] = [c for c in log.commands if c.kind == "subscribe"]
    assert subscribe.expires_at == T0 + timedelta(days=8)


def test_emitted_event_is_deterministic():
    log = check_case(next(c for c in EVENT_CASES if c.name == "emit_publishes_event"))
    [publish, _] = log.commands
    event = publish.event
    assert (event.id, event.source, event.subject, event.time, event.data, event.attributes) == (
        "f-1/notify@1.1/emit",
        "fabula:f-1",
        "T-1",
        T0,
        {"text": "done T-1"},
        {"tenant": "acme"},
    )
