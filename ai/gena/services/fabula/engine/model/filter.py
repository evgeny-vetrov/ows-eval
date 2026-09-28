"""Event filter tree: `all | any | not | predicate(attr, op, value)`.

Attribute paths are dot-separated and relative to the event envelope
(`type`, `source`, `subject`, `data.pr.id`, `attributes.tenant`). The external
subscription system understands the same tree, so an adapter translates it one to one.
"""

from typing import Annotated, Literal, Union

from pydantic import Field

from ai.gena.services.fabula.engine.model.base import Frozen
from ai.gena.services.fabula.engine.model.jsonvalue import Json

FilterOp = Literal[
    "eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains", "icontains", "matches", "exists"
]
FILTER_OPS: tuple[str, ...] = FilterOp.__args__


class Predicate(Frozen):
    kind: Literal["predicate"] = "predicate"
    attr: str
    op: FilterOp
    value: Json = None


class AllOf(Frozen):
    kind: Literal["all"] = "all"
    filters: tuple["Filter", ...]


class AnyOf(Frozen):
    kind: Literal["any"] = "any"
    filters: tuple["Filter", ...]


class NotOf(Frozen):
    kind: Literal["not"] = "not"
    filter: "Filter"


Filter = Annotated[Union[Predicate, AllOf, AnyOf, NotOf], Field(discriminator="kind")]

AllOf.model_rebuild()
AnyOf.model_rebuild()
NotOf.model_rebuild()


def eq(attr: str, value: Json) -> Predicate:
    return Predicate(attr=attr, op="eq", value=value)


def all_of(*filters: Filter) -> Filter:
    return filters[0] if len(filters) == 1 else AllOf(filters=filters)


def any_of(*filters: Filter) -> Filter:
    return filters[0] if len(filters) == 1 else AnyOf(filters=filters)


def attribute_paths(node: Filter) -> list[str]:
    if isinstance(node, Predicate):
        return [node.attr]
    if isinstance(node, NotOf):
        return attribute_paths(node.filter)
    return [path for child in node.filters for path in attribute_paths(child)]
