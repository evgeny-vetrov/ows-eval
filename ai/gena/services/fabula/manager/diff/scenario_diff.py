"""Pure diff of two published versions of a scenario.

Nodes are matched by their path (paths are built from task names, so they survive
insertions and reordering). A node's own definition is its document fragment with
child task lists replaced by the list of child names: a change inside a child shows up
on the child, a change of the list itself (added, removed, reordered children) on the
parent.

`static_compat` looks only at what `engine/core/swap.py` compares between frames (task
kind, scope, capability ref, listen filters, `fork.compete`). Whatever depends on the
running state is left to `analyze_swap` of each fabula.
"""

import difflib
from collections.abc import Iterator
from typing import Any

from ai.gena.services.fabula.engine.dsl.graph import ROOT, Graph, Node
from ai.gena.services.fabula.manager.model.diff import (
    DescriptorChange,
    FieldChange,
    MappingHint,
    NodeChange,
    ScenarioDiff,
    StaticCompat,
    WorkflowChanges,
)
from ai.gena.services.fabula.manager.model.scenarios import ScenarioVersion

SUGGEST_THRESHOLD = 0.8
_IGNORED_WORKFLOW_FIELDS = ("/document/version",)


def _tokens(pointer: str) -> list[str]:
    if not pointer:
        return []
    return [token.replace("~1", "/").replace("~0", "~") for token in pointer.split("/")[1:]]


def _escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def resolve_pointer(document: Any, pointer: str) -> Any:
    value = document
    for token in _tokens(pointer):
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def _replace(value: Any, tokens: list[str], replacement: Any) -> Any:
    if not tokens:
        return replacement
    head, rest = tokens[0], tokens[1:]
    if isinstance(value, list):
        index = int(head)
        return [_replace(item, rest, replacement) if i == index else item for i, item in enumerate(value)]
    if isinstance(value, dict) and head in value:
        return {key: _replace(item, rest, replacement) if key == head else item for key, item in value.items()}
    return value


def own_definition(document: Any, graph: Graph, node: Node) -> Any:
    """The node's fragment with each child task list replaced by the children's names."""
    fragment = resolve_pointer(document, node.pointer)
    for children in node.children.values():
        if not children:
            continue
        first = graph.nodes[children[0]]
        list_tokens = _tokens(first.pointer)[len(_tokens(node.pointer)) : -2]
        fragment = _replace(fragment, list_tokens, [graph.nodes[child].name for child in children])
    return fragment


def json_diff(old: Any, new: Any, pointer: str = "") -> Iterator[FieldChange]:
    if isinstance(old, dict) and isinstance(new, dict):
        for key in old:
            if key not in new:
                yield FieldChange(pointer=f"{pointer}/{_escape(key)}", change="removed", old=old[key])
        for key in new:
            if key not in old:
                yield FieldChange(pointer=f"{pointer}/{_escape(key)}", change="added", new=new[key])
            else:
                yield from json_diff(old[key], new[key], f"{pointer}/{_escape(key)}")
    elif isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        for index, (a, b) in enumerate(zip(old, new)):
            yield from json_diff(a, b, f"{pointer}/{index}")
    elif type(old) is not type(new) or old != new:
        yield FieldChange(pointer=pointer or "/", change="changed", old=old, new=new)


def static_compat(old: Node, new: Node) -> tuple[StaticCompat, tuple[str, ...]]:
    breaking = []
    if old.kind != new.kind:
        breaking.append(f"task kind changed from {old.kind} to {new.kind}")
    if old.parent != new.parent or old.list_key != new.list_key:
        breaking.append("the node moved to another scope")
    if old.kind == new.kind == "call" and old.capability_ref != new.capability_ref:
        breaking.append(f"capability changed from {old.capability_ref} to {new.capability_ref}")
    if old.kind == new.kind == "listen" and old.compat_key != new.compat_key:
        breaking.append("event filters or consumption strategy changed")
    if old.kind == new.kind == "fork" and old.compete != new.compete:
        breaking.append("fork.compete changed")
    if breaking:
        return "breaking", tuple(breaking)
    depends = []
    if old.kind == "try" and old.list("catch") and not new.list("catch"):
        depends.append("catch.do was removed: a fabula inside the catch cannot stay")
    if old.kind == "fork":
        gone = sorted(set(old.list("branches")) - set(new.list("branches")))
        if gone:
            depends.append(f"branches removed ({', '.join(gone)}): a fabula running them cannot stay")
    if depends:
        return "depends_on_state", tuple(depends)
    return "compatible", ("a fabula on this node keeps its operations and finishes it under the new definition",)


def _hint(old: Node, new: Node, old_definition: Any, new_definition: Any) -> MappingHint | None:
    if old.kind != new.kind or old.parent != new.parent or old.list_key != new.list_key:
        return None
    score, reasons = 0.5, ["same task kind", "same scope"]
    if old.kind == "call":
        if old.capability_ref == new.capability_ref:
            score, reasons = score + 0.3, [*reasons, "same capability"]
    elif old.kind == "listen":
        if old.compat_key == new.compat_key:
            score, reasons = score + 0.3, [*reasons, "same event filters"]
    if old_definition == new_definition:
        score = 1.0 if old.kind in ("call", "listen") else score + 0.5
        reasons.append("same definition")
    return MappingHint(old_node=old.path, new_node=new.path, score=round(min(score, 1.0), 3), reasons=tuple(reasons))


def _suggest(hints: list[MappingHint]) -> dict[str, str]:
    best_old: dict[str, float] = {}
    best_new: dict[str, float] = {}
    for hint in hints:
        best_old[hint.old_node] = max(best_old.get(hint.old_node, 0), hint.score)
        best_new[hint.new_node] = max(best_new.get(hint.new_node, 0), hint.score)
    suggested = {}
    for hint in hints:
        if hint.score < SUGGEST_THRESHOLD or hint.score < best_old[hint.old_node] or hint.score < best_new[hint.new_node]:
            continue
        rivals = [h for h in hints if h is not hint and h.score == hint.score and (h.old_node == hint.old_node or h.new_node == hint.new_node)]
        if not rivals:
            suggested[hint.old_node] = hint.new_node
    return suggested


def _descriptors(catalog: str, old: dict, new: dict) -> Iterator[DescriptorChange]:
    for ref in sorted(set(old) | set(new)):
        if ref not in new:
            yield DescriptorChange(catalog=catalog, ref=ref, change="removed")
        elif ref not in old:
            yield DescriptorChange(catalog=catalog, ref=ref, change="added")
        else:
            fields = tuple(json_diff(old[ref].model_dump(mode="json"), new[ref].model_dump(mode="json")))
            if fields:
                yield DescriptorChange(catalog=catalog, ref=ref, change="changed", fields=fields)


def diff_versions(old: ScenarioVersion, new: ScenarioVersion, old_graph: Graph, new_graph: Graph) -> ScenarioDiff:
    old_doc, new_doc = old.snapshot.document, new.snapshot.document
    definitions_old = {path: own_definition(old_doc, old_graph, node) for path, node in old_graph.nodes.items()}
    definitions_new = {path: own_definition(new_doc, new_graph, node) for path, node in new_graph.nodes.items()}
    workflow_fields = tuple(f for f in json_diff(definitions_old[ROOT], definitions_new[ROOT]) if f.pointer not in _IGNORED_WORKFLOW_FIELDS)
    workflow = WorkflowChanges(
        fields=workflow_fields,
        context_keys_added=tuple(k for k in new_graph.context_keys if k not in old_graph.context_keys),
        context_keys_removed=tuple(k for k in old_graph.context_keys if k not in new_graph.context_keys),
    )
    changes = []
    for path in sorted((set(old_graph.nodes) | set(new_graph.nodes)) - {ROOT}):
        before, after = old_graph.nodes.get(path), new_graph.nodes.get(path)
        if after is None:
            changes.append(
                NodeChange(path=path, change="removed", old_kind=before.kind, static_compat="breaking", reasons=("the node was removed: a fabula here needs a mapping",))
            )
        elif before is None:
            changes.append(NodeChange(path=path, change="added", new_kind=after.kind))
        else:
            fields = tuple(json_diff(definitions_old[path], definitions_new[path]))
            compat, reasons = static_compat(before, after)
            if fields or compat != "compatible":
                changes.append(NodeChange(path=path, change="changed", old_kind=before.kind, new_kind=after.kind, fields=fields, static_compat=compat, reasons=reasons))
    removed = [c.path for c in changes if c.change == "removed"]
    added = [c.path for c in changes if c.change == "added"]
    hints = []
    for old_path in removed:
        for new_path in added:
            hint = _hint(old_graph.nodes[old_path], new_graph.nodes[new_path], definitions_old[old_path], definitions_new[new_path])
            if hint is not None:
                hints.append(hint)
    hints.sort(key=lambda h: (-h.score, h.old_node, h.new_node))
    text = "".join(
        difflib.unified_diff(old.source.splitlines(keepends=True), new.source.splitlines(keepends=True), fromfile=old.ref, tofile=new.ref)
    )
    return ScenarioDiff(
        from_ref=old.ref,
        to_ref=new.ref,
        identical=old.digest == new.digest,
        workflow=workflow,
        nodes=tuple(changes),
        descriptors=(*_descriptors("capability", old.snapshot.capabilities, new.snapshot.capabilities), *_descriptors("event", old.snapshot.events, new.snapshot.events)),
        mapping_hints=tuple(hints),
        suggested_mapping=_suggest(hints),
        text_diff=text,
    )
