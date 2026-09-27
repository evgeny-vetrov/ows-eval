import re
from functools import lru_cache
from typing import Any

from ai.gena.services.fabula.engine.model.event import Event
from ai.gena.services.fabula.engine.model.filter import AllOf, AnyOf, Filter, NotOf, Predicate
from ai.gena.services.fabula.engine.model.jsonvalue import MISSING, get_path, is_number, json_equal


@lru_cache(maxsize=256)
def _regex(pattern: str) -> re.Pattern:
    return re.compile(pattern)


def _ordered(left: Any, right: Any) -> bool:
    return (is_number(left) and is_number(right)) or (isinstance(left, str) and isinstance(right, str))


def evaluate_predicate(predicate: Predicate, envelope: dict[str, Any]) -> bool:
    actual = get_path(envelope, predicate.attr.split("."))
    op, expected = predicate.op, predicate.value
    if op == "exists":
        return (actual is not MISSING and actual is not None) == bool(expected)
    if actual is MISSING or actual is None:
        return False
    if op == "eq":
        return json_equal(actual, expected)
    if op == "ne":
        return not json_equal(actual, expected)
    if op in ("gt", "gte", "lt", "lte"):
        if not _ordered(actual, expected):
            return False
        return {"gt": actual > expected, "gte": actual >= expected, "lt": actual < expected, "lte": actual <= expected}[op]
    if op in ("in", "not_in"):
        if not isinstance(expected, list):
            return False
        found = any(json_equal(actual, item) for item in expected)
        return found if op == "in" else not found
    if op == "contains":
        if isinstance(actual, str):
            return isinstance(expected, str) and expected in actual
        if isinstance(actual, list):
            return any(json_equal(item, expected) for item in actual)
        return False
    if op == "icontains":
        if not isinstance(expected, str):
            return False
        if isinstance(actual, str):
            return expected.casefold() in actual.casefold()
        if isinstance(actual, list):
            return any(isinstance(item, str) and item.casefold() == expected.casefold() for item in actual)
        return False
    if op == "matches":
        return isinstance(actual, str) and isinstance(expected, str) and _regex(expected).search(actual) is not None
    raise ValueError(f"unknown filter operator '{op}'")


def evaluate_filter(node: Filter, envelope: dict[str, Any]) -> bool:
    if isinstance(node, Predicate):
        return evaluate_predicate(node, envelope)
    if isinstance(node, AllOf):
        return all(evaluate_filter(child, envelope) for child in node.filters)
    if isinstance(node, AnyOf):
        return any(evaluate_filter(child, envelope) for child in node.filters)
    if isinstance(node, NotOf):
        return not evaluate_filter(node.filter, envelope)
    raise TypeError(f"unknown filter node {type(node).__name__}")


class FilterEventMatcher:
    """Reference `EventMatcher`. Pure; also used by the in-memory event bus.

    A predicate on a missing (or null) attribute is false for every operator except
    `exists: false`. `matches` is an unanchored regular expression search.
    """

    def matches(self, filter: Filter, event: Event) -> bool:
        return evaluate_filter(filter, event.envelope())
