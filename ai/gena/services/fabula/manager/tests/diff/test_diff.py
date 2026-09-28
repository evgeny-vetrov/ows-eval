import asyncio

import pytest

from ai.gena.services.fabula.engine.model.catalog import CapabilityDescriptor
from ai.gena.services.fabula.manager.diff.scenario_diff import json_diff
from ai.gena.services.fabula.manager.testing.scenarios import SCENARIO, V1, V2, V2_RENAMED, V3, V4, fleet, publish
from ai.gena.services.fabula.manager.testing.stand import Stand


def run(coroutine):
    return asyncio.run(coroutine)


def _diff(old: str, new: str, stand: Stand | None = None):
    stand = stand or Stand()

    async def scenario():
        await publish(stand, old, "1.0.0")
        await publish(stand, new, "2.0.0")
        return await stand.manager.diffs.diff(SCENARIO, "1.0.0", "2.0.0")

    return run(scenario())


def _nodes(diff):
    return {c.path: (c.change, c.static_compat) for c in diff.nodes}


def test_identical_versions_differ_only_in_the_header():
    diff = _diff(V1, V1)
    assert diff.nodes == () and diff.workflow.fields == () and diff.descriptors == ()
    assert not diff.identical, "the version string is part of the digest"
    assert "-  version: '1.0.0'" in diff.text_diff and "+  version: '2.0.0'" in diff.text_diff


def test_a_changed_tail_is_compatible():
    diff = _diff(V1, V2)
    [change] = diff.nodes
    assert (change.path, change.change, change.static_compat) == ("/finish", "changed", "compatible")
    assert [(f.pointer, f.change, f.new) for f in change.fields] == [
        ("/set/version", "changed", 2),
        ("/set/ticket", "added", "${ $context.ticket }"),
    ]


def test_a_rename_is_a_removal_and_an_addition_with_a_suggested_mapping():
    diff = _diff(V1, V2_RENAMED)
    assert _nodes(diff) == {
        "/merged": ("removed", "breaking"),
        "/prMerged": ("added", "not_applicable"),
        "/finish": ("changed", "compatible"),
    }
    [hint] = diff.mapping_hints
    assert (hint.old_node, hint.new_node, hint.score) == ("/merged", "/prMerged", 1.0)
    assert diff.suggested_mapping == {"/merged": "/prMerged"}
    [workflow_change] = diff.workflow.fields
    assert (workflow_change.pointer, workflow_change.old, workflow_change.new) == ("/do/2", "merged", "prMerged")


def test_incompatible_changes_are_flagged_with_reasons():
    diff = _diff(V1, V3)
    changes = {c.path: c for c in diff.nodes}
    assert changes["/ask"].static_compat == "breaking"
    assert changes["/ask"].reasons == ("capability changed from echo:1@test to strict:1@test",)
    assert changes["/merged"].reasons == ("event filters or consumption strategy changed",)
    assert changes["/finish"].static_compat == "compatible"
    assert diff.workflow.context_keys_added == ("owner",)
    assert [(d.catalog, d.ref, d.change) for d in diff.descriptors] == [("capability", "echo:1@test", "removed"), ("capability", "strict:1@test", "added")]
    assert diff.suggested_mapping == {}


def test_a_removed_node_without_a_candidate_gets_no_suggestion():
    diff = _diff(V1, V4)
    assert _nodes(diff)["/ask"] == ("removed", "breaking")
    assert diff.mapping_hints == ()


def test_descriptor_changes_are_reported_field_by_field():
    stand = Stand()
    run(publish(stand, V1, "1.0.0"))
    stand.deps.capabilities.add(CapabilityDescriptor(ref="echo:1@test", effects=("none",), title="Echo"))
    run(publish(stand, V1, "2.0.0"))
    diff = run(stand.manager.diffs.diff(SCENARIO, "1.0.0", "2.0.0"))
    [change] = diff.descriptors
    assert (change.ref, change.change, [(f.pointer, f.new) for f in change.fields]) == ("echo:1@test", "changed", [("/title", "Echo")])


@pytest.mark.parametrize(
    "old, new, expected",
    [
        ({"a": 1}, {"a": 1}, []),
        ({"a": 1}, {"a": 2}, [("/a", "changed")]),
        ({"a": 1}, {}, [("/a", "removed")]),
        ([1, 2], [1, 3], [("/1", "changed")]),
        ([1, 2], [1, 2, 3], [("/", "changed")]),
        ({"a/b": {"c": True}}, {"a/b": {"c": 1}}, [("/a~1b/c", "changed")]),
    ],
)
def test_json_diff(old, new, expected):
    assert [(f.pointer, f.change) for f in json_diff(old, new)] == expected


@pytest.mark.parametrize("new, version", [(V2, "2.0.0"), (V2_RENAMED, "2.1.0"), (V3, "3.0.0"), (V4, "4.0.0")])
def test_static_compat_agrees_with_the_analysis_of_waiting_fabulas(new, version):
    """A fabula waiting on a node the diff calls compatible keeps it; one waiting on a
    breaking node needs a mapping."""
    stand = Stand()

    async def scenario():
        await publish(stand, V1, "1.0.0")
        ids = await fleet(stand)
        target = await publish(stand, new, version)
        diff = await stand.manager.diffs.diff(SCENARIO, "1.0.0", version)
        compat = {c.path: c.static_compat for c in diff.nodes}
        found = []
        for role in ("at_call", "at_event"):
            report = await stand.runtime.analyze_swap(ids[role], target.ref)
            for move in report.cursor:
                found.append((compat.get(move.old_node, "compatible"), move.disposition))
            suggested = await stand.runtime.analyze_swap(ids[role], target.ref, diff.suggested_mapping)
            found.append(("suggested", suggested.compatible or suggested.problems != ()))
        return found

    for compat, disposition in run(scenario()):
        if compat == "compatible":
            assert disposition == "kept"
        elif compat == "breaking":
            assert disposition == "unmapped"
        else:
            assert disposition is True
