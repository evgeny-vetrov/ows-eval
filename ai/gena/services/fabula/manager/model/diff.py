from typing import Literal

from pydantic import Field

from ai.gena.services.fabula.engine.model.base import Frozen
from ai.gena.services.fabula.engine.model.jsonvalue import Json

ChangeKind = Literal["added", "removed", "changed"]
# Static advice about a fabula standing on the node when the swap happens. Only
# `analyze_swap` on the fabula's state is authoritative.
StaticCompat = Literal["compatible", "breaking", "depends_on_state", "not_applicable"]


class FieldChange(Frozen):
    pointer: str  # relative to the node, e.g. "/call" or "/with/ticket"
    change: ChangeKind
    old: Json = None
    new: Json = None


class NodeChange(Frozen):
    path: str
    change: ChangeKind
    old_kind: str | None = None
    new_kind: str | None = None
    fields: tuple[FieldChange, ...] = ()
    static_compat: StaticCompat = "not_applicable"
    reasons: tuple[str, ...] = ()


class DescriptorChange(Frozen):
    catalog: Literal["capability", "event"]
    ref: str
    change: ChangeKind
    fields: tuple[FieldChange, ...] = ()


class WorkflowChanges(Frozen):
    fields: tuple[FieldChange, ...] = ()
    context_keys_added: tuple[str, ...] = ()
    context_keys_removed: tuple[str, ...] = ()


class MappingHint(Frozen):
    old_node: str
    new_node: str
    score: float
    reasons: tuple[str, ...] = ()


class ScenarioDiff(Frozen):
    from_ref: str
    to_ref: str
    identical: bool
    workflow: WorkflowChanges = WorkflowChanges()
    nodes: tuple[NodeChange, ...] = ()
    descriptors: tuple[DescriptorChange, ...] = ()
    mapping_hints: tuple[MappingHint, ...] = ()
    # Unique best hints: the default mapping of a migration campaign.
    suggested_mapping: dict[str, str] = Field(default_factory=dict)
    text_diff: str = ""
