import pytest

from ai.gena.services.fabula.engine.model.event import Event
from ai.gena.services.fabula.engine.model.filter import AllOf, AnyOf, NotOf, Predicate
from ai.gena.services.fabula.engine.testing.matcher import FilterEventMatcher

EVENT = Event(
    id="e-1",
    type="vcs.pr.merged",
    source="git",
    subject="PR-7",
    data={"pr": {"id": 7, "title": "Fix Login"}, "labels": ["bug", "UI"], "size": 12, "flag": True},
    attributes={"tenant": "acme"},
)


@pytest.mark.parametrize(
    "attr, op, value, expected",
    [
        ("type", "eq", "vcs.pr.merged", True),
        ("data.pr.id", "eq", 7, True),
        ("data.pr.id", "eq", 7.0, True),
        ("data.flag", "eq", 1, False),
        ("data.pr.id", "ne", 8, True),
        ("data.size", "gt", 11, True),
        ("data.size", "gte", 12, True),
        ("data.size", "lt", 12, False),
        ("data.size", "lte", 12, True),
        ("data.size", "gt", "11", False),
        ("subject", "in", ["PR-6", "PR-7"], True),
        ("subject", "not_in", ["PR-6", "PR-7"], False),
        ("data.labels", "contains", "bug", True),
        ("data.pr.title", "contains", "Login", True),
        ("data.pr.title", "icontains", "login", True),
        ("data.labels", "icontains", "ui", True),
        ("data.pr.title", "matches", "^Fix", True),
        ("data.pr.title", "matches", "Log", True),
        ("attributes.tenant", "exists", True, True),
        ("attributes.region", "exists", False, True),
        ("data.labels.1", "eq", "UI", True),
        # A predicate on a missing attribute is false for every operator except exists.
        ("data.missing", "ne", 1, False),
        ("data.missing", "not_in", [1], False),
    ],
)
def test_predicate_operators(attr, op, value, expected):
    assert FilterEventMatcher().matches(Predicate(attr=attr, op=op, value=value), EVENT) is expected


def test_tree_combinators():
    matcher = FilterEventMatcher()
    merged = Predicate(attr="type", op="eq", value="vcs.pr.merged")
    other_pr = Predicate(attr="data.pr.id", op="eq", value=8)
    assert matcher.matches(AllOf(filters=(merged, NotOf(filter=other_pr))), EVENT)
    assert not matcher.matches(AllOf(filters=(merged, other_pr)), EVENT)
    assert matcher.matches(AnyOf(filters=(other_pr, merged)), EVENT)


def test_filter_survives_json_round_trip():
    tree = AllOf(filters=(Predicate(attr="type", op="eq", value="x"), NotOf(filter=Predicate(attr="subject", op="exists", value=True))))
    assert AllOf.model_validate_json(tree.model_dump_json()) == tree
