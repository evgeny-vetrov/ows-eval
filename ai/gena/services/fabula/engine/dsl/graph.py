"""Compiled scenario graph. Built from a validated document; never serialized.

Node ids are paths by task names (`/runAgent`, `/guard/try/call`, `/par/left/step`),
so they stay stable when tasks are inserted or reordered.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from ai.gena.services.fabula.engine.model.catalog import Backoff, CapabilityDescriptor, EventTypeDescriptor
from ai.gena.services.fabula.engine.ports.expressions import CompiledExpression

ROOT = "/"
DIRECTIVES = ("continue", "exit", "end")


@dataclass(frozen=True)
class Expr:
    text: str
    compiled: CompiledExpression
    pointer: str


@dataclass(frozen=True)
class Template:
    """A JSON value whose strings may be runtime expressions."""

    kind: str  # const | expr | object | array
    value: Any = None
    expr: Expr | None = None
    items: tuple = ()

    @property
    def is_const(self) -> bool:
        return self.kind == "const"


@dataclass(frozen=True)
class Timeout:
    after: timedelta | None = None
    expr: Template | None = None


@dataclass(frozen=True)
class RetrySpec:
    delay: timedelta
    backoff: Backoff
    jitter_from: timedelta
    jitter_to: timedelta
    limit_count: int | None
    limit_attempt_duration: timedelta | None
    limit_duration: timedelta | None
    when: Expr | None
    except_when: Expr | None


@dataclass(frozen=True)
class CatchSpec:
    errors_with: dict[str, Any]
    as_name: str
    when: Expr | None
    except_when: Expr | None
    retry: RetrySpec | None


@dataclass(frozen=True)
class Correlation:
    key: str
    attr: str
    expect: Template


@dataclass(frozen=True)
class EventFilterSpec:
    event_type: str
    predicates: tuple[tuple[str, Template], ...]
    correlations: tuple[Correlation, ...]
    pointer: str


@dataclass(frozen=True)
class StrategySpec:
    mode: str  # one | any | all
    filters: tuple[EventFilterSpec, ...]
    until_expr: Expr | None = None
    until: "StrategySpec | None" = None


@dataclass(frozen=True)
class ListenSpec:
    strategy: StrategySpec
    read: str
    foreach: bool
    item: str
    at: str
    foreach_output_as: Template | None
    foreach_export_as: Template | None


@dataclass(frozen=True)
class SwitchCase:
    name: str
    when: Expr | None
    then: str


@dataclass(frozen=True)
class Node:
    path: str
    name: str
    kind: str
    pointer: str
    parent: str | None
    list_key: str | None
    depth: int
    children: dict[str, tuple[str, ...]] = field(default_factory=dict)
    condition: Expr | None = None
    input_from: Template | None = None
    output_as: Template | None = None
    export_as: Template | None = None
    input_schema: dict | None = None
    output_schema: dict | None = None
    export_schema: dict | None = None
    timeout: Timeout | None = None
    then: str = "continue"
    # kind-specific
    template: Template | None = None  # set, call.with, raise error, emit event
    cases: tuple[SwitchCase, ...] = ()
    wait_for: Timeout | None = None  # wait duration
    wait_until: Template | None = None
    capability_ref: str | None = None
    capability: CapabilityDescriptor | None = None
    catch: CatchSpec | None = None
    for_each: str = "item"
    for_at: str = "index"
    for_in: Expr | None = None
    for_while: Expr | None = None
    listen: ListenSpec | None = None
    emit_type: EventTypeDescriptor | None = None
    compete: bool = False
    # what must match for an in-flight operation to survive a version swap
    compat_key: str = ""

    def list(self, key: str) -> tuple[str, ...]:
        return self.children.get(key, ())


@dataclass(frozen=True)
class WorkflowSpec:
    namespace: str
    name: str
    version: str
    dsl: str
    input_schema: dict | None
    input_from: Template | None
    output_as: Template | None
    output_schema: dict | None
    timeout: Timeout | None
    schedule_after: timedelta | None


@dataclass(frozen=True)
class Graph:
    digest: str
    workflow: WorkflowSpec
    nodes: dict[str, Node]
    context_keys: tuple[str, ...]

    def node(self, path: str) -> Node:
        return self.nodes[path]

    def ancestors(self, path: str) -> list[str]:
        """Paths from the root down to (excluding) `path`."""
        chain = []
        current = self.nodes[path].parent
        while current is not None:
            chain.append(current)
            current = self.nodes[current].parent
        return list(reversed(chain))


def child_path(parent: str, list_key: str, name: str) -> str:
    prefix = "" if parent == ROOT else parent
    if list_key in ("try", "catch"):
        return f"{prefix}/{list_key}/{name}"
    return f"{prefix}/{name}"
