import asyncio
from collections import Counter

import pytest
from pydantic import ValidationError

from ai.gena.services.fabula.manager.model.errors import NotFound, RevisionConflict
from ai.gena.services.fabula.manager.model.fabulas import Selector
from ai.gena.services.fabula.manager.model.tags import TRUNK, Allocation, Arm, MoveTag, RollbackTag, SetAllocation
from ai.gena.services.fabula.manager.registry.routing import bucket_of, pick_arm
from ai.gena.services.fabula.manager.testing.scenarios import SCENARIO, V1, V2, V3, fleet, publish, start
from ai.gena.services.fabula.manager.testing.stand import ALICE, Stand


def run(coroutine):
    return asyncio.run(coroutine)


def _stand_with_versions(*versions: tuple[str, str]) -> Stand:
    stand = Stand()

    async def scenario():
        for body, version in versions:
            await publish(stand, body, version)

    run(scenario())
    return stand


def test_move_keeps_an_append_only_history_and_rollback_restores_a_revision():
    stand = _stand_with_versions((V1, "1.0.0"), (V2, "2.0.0"), (V3, "3.0.0"))
    tags = stand.manager.tags

    async def scenario():
        await tags.move(ALICE, SCENARIO, TRUNK, MoveTag(request_id="m2", version="2.0.0", reason="ship v2", expected_revision=1))
        await tags.move(ALICE, SCENARIO, TRUNK, MoveTag(request_id="m3", version="3.0.0", reason="ship v3"))
        back = await tags.rollback(ALICE, SCENARIO, TRUNK, RollbackTag(request_id="r", to_revision=2, reason="v3 misbehaves"))
        pinned = await tags.rollback(ALICE, SCENARIO, TRUNK, RollbackTag(request_id="r1", to_version="1.0.0"))
        return back, pinned, await tags.history(SCENARIO, TRUNK)

    back, pinned, history = run(scenario())
    assert (back.tag.allocation.single_version, back.tag.revision) == ("2.0.0", 4)
    assert pinned.tag.allocation.single_version == "1.0.0"
    assert [(c.revision, c.kind, c.allocation.single_version, c.actor) for c in history.items] == [
        (1, "create", "1.0.0", "user:alice"),
        (2, "move", "2.0.0", "user:alice"),
        (3, "move", "3.0.0", "user:alice"),
        (4, "rollback", "2.0.0", "user:alice"),
        (5, "rollback", "1.0.0", "user:alice"),
    ]
    assert [c.reason for c in history.items][1:4] == ["ship v2", "ship v3", "v3 misbehaves"]


def test_tag_changes_are_idempotent_and_locked():
    stand = _stand_with_versions((V1, "1.0.0"), (V2, "2.0.0"))
    tags = stand.manager.tags

    async def scenario():
        first = await tags.move(ALICE, SCENARIO, TRUNK, MoveTag(request_id="same", version="2.0.0"))
        repeated = await tags.move(ALICE, SCENARIO, TRUNK, MoveTag(request_id="same", version="2.0.0"))
        unchanged = await tags.move(ALICE, SCENARIO, TRUNK, MoveTag(request_id="again", version="2.0.0"))
        with pytest.raises(RevisionConflict):
            await tags.move(ALICE, SCENARIO, TRUNK, MoveTag(request_id="stale", version="1.0.0", expected_revision=1))
        with pytest.raises(NotFound):
            await tags.move(ALICE, SCENARIO, "canary", MoveTag(request_id="c", version="1.0.0", expected_revision=3))
        with pytest.raises(NotFound):
            await tags.rollback(ALICE, SCENARIO, TRUNK, RollbackTag(request_id="r", to_revision=42))
        return first, repeated, unchanged, await tags.history(SCENARIO, TRUNK)

    first, repeated, unchanged, history = run(scenario())
    assert first.tag == repeated.tag == unchanged.tag
    assert len(history.items) == 2


def test_new_tags_are_created_next_to_trunk():
    stand = _stand_with_versions((V1, "1.0.0"), (V2, "2.0.0"))
    result = run(stand.manager.tags.move(ALICE, SCENARIO, "canary", MoveTag(request_id="c", version="2.0.0")))
    listed = run(stand.manager.tags.list(SCENARIO))
    assert (result.tag.revision, result.campaign_id) == (1, None)
    assert [(t.tag, t.allocation.single_version) for t in listed.items] == [("canary", "2.0.0"), (TRUNK, "1.0.0")]


@pytest.mark.parametrize(
    "arms, error",
    [
        ((), "at least one arm"),
        ((Arm(arm="a", version="1", weight=5000), Arm(arm="a", version="2", weight=5000)), "unique"),
        ((Arm(arm="a", version="1", weight=5000), Arm(arm="b", version="2", weight=4000)), "add up"),
    ],
)
def test_allocations_are_validated(arms, error):
    with pytest.raises(ValidationError, match=error):
        Allocation(arms=arms)


def test_routing_is_deterministic_sticky_and_follows_the_weights():
    allocation = Allocation(arms=(Arm(arm="control", version="1.0.0", weight=7000), Arm(arm="treatment", version="2.0.0", weight=3000)), salt="exp-1")
    arms = [pick_arm(allocation, bucket_of(SCENARIO, TRUNK, allocation.salt, f"user-{i}")).arm for i in range(10_000)]
    assert arms == [pick_arm(allocation, bucket_of(SCENARIO, TRUNK, allocation.salt, f"user-{i}")).arm for i in range(10_000)]
    share = Counter(arms)["treatment"] / len(arms)
    assert 0.28 < share < 0.32
    resalted = [pick_arm(allocation, bucket_of(SCENARIO, TRUNK, "exp-2", f"user-{i}")).arm for i in range(10_000)]
    assert resalted != arms


def test_ab_allocation_routes_starts_and_records_the_assignment():
    stand = _stand_with_versions((V1, "1.0.0"), (V2, "2.0.0"))
    allocation = Allocation(arms=(Arm(arm="a", version="1.0.0", weight=5000), Arm(arm="b", version="2.0.0", weight=5000)), salt="exp")

    async def scenario():
        await stand.manager.tags.allocate(ALICE, SCENARIO, TRUNK, SetAllocation(request_id="ab", allocation=allocation))
        results = [await start(stand, f"s-{i}", routing_key=f"user-{i % 20}") for i in range(40)]
        return results, [await stand.index.get(r.fabula_id) for r in results], [await stand.runtime.state(r.fabula_id) for r in results]

    results, records, states = run(scenario())
    assert {r.assignment.arm for r in results} == {"a", "b"}
    by_key: dict[str, set[str]] = {}
    for i, result in enumerate(results):
        by_key.setdefault(f"user-{i % 20}", set()).add(result.assignment.arm)
    assert all(len(arms) == 1 for arms in by_key.values()), "the same routing key sticks to one arm"
    for result, record, state in zip(results, records, states):
        assert record.assignment == result.assignment
        assert state.scenario.ref == result.ref
        labels = state.execution_context.labels
        assert (labels["fabula.tag"], labels["fabula.arm"], labels["fabula.version"]) == (TRUNK, result.assignment.arm, result.assignment.version)


def test_moving_trunk_asks_about_running_fabulas_and_moves_none_of_them():
    stand = _stand_with_versions((V1, "1.0.0"))

    async def scenario():
        ids = await fleet(stand)
        await publish(stand, V2, "2.0.0")
        moved = await stand.manager.tags.move(ALICE, SCENARIO, TRUNK, MoveTag(request_id="ship", version="2.0.0"))
        campaign = await stand.manager.migrations.get(moved.campaign_id)
        refs = {role: (await stand.runtime.state(fid)).scenario.ref for role, fid in ids.items()}
        new = await start(stand, "after-move")
        return moved, campaign, refs, new

    moved, campaign, refs, new = run(scenario())
    assert campaign.status == "awaiting_decision" and campaign.origin == "tag_move"
    assert campaign.counts == {"state.pending": 3, "verdict.in_flight": 3}
    assert set(refs.values()) == {"test/order:1.0.0"}, "nothing moves without a decision"
    assert new.ref == "test/order:2.0.0"
    assert "campaign.awaiting_decision" in stand.notifier.kinds()


def test_no_campaign_is_proposed_without_running_fabulas_or_when_asked_not_to():
    stand = _stand_with_versions((V1, "1.0.0"), (V2, "2.0.0"))
    quiet = run(stand.manager.tags.move(ALICE, SCENARIO, TRUNK, MoveTag(request_id="a", version="2.0.0")))
    run(start(stand, "one", selector=Selector(version="1.0.0")))
    untagged = run(stand.manager.tags.move(ALICE, SCENARIO, TRUNK, MoveTag(request_id="b", version="1.0.0")))
    run(start(stand, "two"))
    declined = run(stand.manager.tags.move(ALICE, SCENARIO, TRUNK, MoveTag(request_id="c", version="2.0.0", propose_migration=False)))
    assert (quiet.campaign_id, untagged.campaign_id, declined.campaign_id) == (None, None, None)


def test_a_replayed_move_opens_the_campaign_an_interrupted_attempt_owed():
    stand = _stand_with_versions((V1, "1.0.0"))
    run(fleet(stand))
    run(publish(stand, V2, "2.0.0"))
    tags = stand.manager.tags
    original = tags.proposer

    async def down(*args):
        tags.proposer = original
        raise ConnectionError("campaign store down")

    tags.proposer = down
    with pytest.raises(ConnectionError):
        run(tags.move(ALICE, SCENARIO, TRUNK, MoveTag(request_id="ship", version="2.0.0")))
    retried = run(tags.move(ALICE, SCENARIO, TRUNK, MoveTag(request_id="ship", version="2.0.0")))
    assert retried.campaign_id is not None and retried.tag.revision == 2
    assert len(run(stand.manager.migrations.list()).items) == 1


def test_a_malformed_tag_is_invalid_input():
    from ai.gena.services.fabula.manager.model.errors import InvalidInput

    stand = _stand_with_versions((V1, "1.0.0"))
    with pytest.raises(InvalidInput) as error:
        run(stand.manager.tags.move(ALICE, SCENARIO, "Bad_Tag", MoveTag(request_id="m", version="1.0.0")))
    assert error.value.code == "bad_tag"
