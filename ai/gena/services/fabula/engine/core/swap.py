"""Moving a fabula to another scenario version.

The same planner backs the pure `analyze_swap` and the `SwapVersion` control, so the
report a caller previews is exactly what the swap then does.

For every active frame (parents first) the target is `mapping[path]` or the same path.
A frame is kept when its target exists, sits under the image of the frame's parent in
the same list, has the same task kind and compatible parameters. A frame that cannot
be kept is restarted at its target if the mapping names it explicitly; otherwise the
swap is rejected. Frames below a restarted frame are dropped with their operations.
"""

from dataclasses import dataclass, field
from typing import Any

from ai.gena.services.fabula.engine.core.control import Reject, RECOVERABLE
from ai.gena.services.fabula.engine.core.evaluation import ScenarioError, evaluate_expr
from ai.gena.services.fabula.engine.core.flow import Flow
from ai.gena.services.fabula.engine.core.state import Enter
from ai.gena.services.fabula.engine.dsl.diagnostics import InvalidScenario
from ai.gena.services.fabula.engine.dsl.graph import ROOT, Expr, Graph, Node
from ai.gena.services.fabula.engine.model.observations import NodeCancelled, ScenarioSwapped
from ai.gena.services.fabula.engine.model.scenario import ScenarioSnapshot
from ai.gena.services.fabula.engine.model.swap import CursorMove, OperationFate, SwapReport
from ai.gena.services.fabula.engine.ports.expressions import ExpressionDiagnostics, expression_body, is_expression


@dataclass
class SwapPlan:
    report: SwapReport
    graph: Graph | None = None
    mapping: dict[str, str] = field(default_factory=dict)
    targets: dict[str, str] = field(default_factory=dict)  # kept frames: old path -> new path
    restarts: list[tuple[str, str]] = field(default_factory=list)  # (old path, new path)
    removed: list[str] = field(default_factory=list)  # restarted or dropped frames
    cancelled_ops: list[str] = field(default_factory=list)
    context: Any = None


def _compatible(old: Node, new: Node, frame) -> str | None:
    if old.kind == "call" and old.capability.ref != new.capability.ref:
        return f"capability changed from {old.capability.ref} to {new.capability.ref}"
    if old.kind == "listen" and old.compat_key != new.compat_key:
        return "event filters or consumption strategy changed"
    if old.kind == "fork" and old.compete != new.compete:
        return "fork.compete changed"
    if old.kind == "try" and frame.phase == "catch" and not new.list("catch"):
        return "catch.do was removed while it runs"
    return None


class Swapping(Flow):
    def plan_swap(self, scenario: ScenarioSnapshot, mapping: dict[str, str], migration: str | None) -> SwapPlan:
        base = dict(applicable=False, compatible=False, from_ref=self.scenario.ref, to_ref=scenario.ref)
        try:
            graph = self.interpreter.graph(scenario)
        except InvalidScenario as exc:
            return SwapPlan(SwapReport(**base, problems=tuple(f"new version is invalid: {d}" for d in exc.diagnostics)))
        problems: list[str] = []
        for old_path, new_path in mapping.items():
            if old_path not in self.graph.nodes:
                problems.append(f"mapping source '{old_path}' is not a node of {self.scenario.ref}")
            if new_path not in graph.nodes:
                problems.append(f"mapping target '{new_path}' is not a node of {scenario.ref}")
        plan = SwapPlan(SwapReport(**base), graph=graph, mapping=dict(mapping))
        moves: list[CursorMove] = []
        decided: dict[str, tuple[str, str | None]] = {ROOT: ("kept", ROOT)}
        plan.targets[ROOT] = ROOT
        for path in sorted(self.frames, key=lambda p: self.graph.nodes[p].depth):
            if path == ROOT:
                continue
            old = self.graph.nodes[path]
            frame = self.frames[path]
            parent_fate, parent_target = decided[old.parent]
            if parent_fate != "kept":
                decided[path] = ("dropped", None)
                moves.append(CursorMove(old_node=path, new_node=None, kind=old.kind, disposition="dropped", reason=f"'{old.parent}' is {parent_fate}"))
                plan.removed.append(path)
                continue
            explicit = path in mapping
            target = mapping.get(path, path)
            new = graph.nodes.get(target)
            if new is None:
                reason = f"'{target}' does not exist in the new version"
            elif new.parent != parent_target or new.list_key != old.list_key:
                reason = f"'{target}' is not in the scope that replaces '{old.parent}'"
            elif new.kind != old.kind:
                reason = f"task kind changed from {old.kind} to {new.kind}"
            else:
                reason = _compatible(old, new, frame)
            if reason is None:
                decided[path] = ("kept", target)
                plan.targets[path] = target
                moves.append(CursorMove(old_node=path, new_node=target, kind=old.kind, disposition="kept"))
            elif explicit and new is not None and new.parent == parent_target and self.graph.nodes[path].list_key == new.list_key:
                decided[path] = ("restarted", target)
                plan.restarts.append((path, target))
                plan.removed.append(path)
                moves.append(CursorMove(old_node=path, new_node=target, kind=old.kind, disposition="restarted", reason=reason))
            else:
                decided[path] = ("unmapped", None)
                hint = "restart targets must stay in the same scope" if explicit else "map it explicitly to restart it"
                problems.append(f"'{path}': {reason}; {hint}")
                moves.append(CursorMove(old_node=path, new_node=None, kind=old.kind, disposition="unmapped", reason=reason))
        for fork_path, target in list(plan.targets.items()):
            frame = self.frames[fork_path]
            if frame.kind != "fork":
                continue
            branches = set(graph.nodes[target].list("branches"))
            for branch in frame.branches:
                fate = decided.get(branch.path)
                new_branch = fate[1] if fate else mapping.get(branch.path, branch.path)
                if branch.status == "running" and new_branch not in branches:
                    problems.append(f"running branch '{branch.path}' has no counterpart in '{target}'")
        operations = []
        for op_id, op in self.inflight.items():
            kept = op.owner in plan.targets
            operations.append(OperationFate(op_id=op_id, op_kind=op.op, old_node=op.owner, disposition="kept" if kept else "cancelled"))
            if not kept:
                plan.cancelled_ops.append(op_id)
        context = self.data
        if migration is not None:
            compiled = self.expressions.compile(expression_body(migration) if is_expression(migration) else migration)
            if isinstance(compiled, ExpressionDiagnostics):
                problems.append(f"context migration does not compile: {'; '.join(compiled.messages)}")
            else:
                try:
                    context = evaluate_expr(self.expressions, Expr(migration, compiled, "/context_migration"), self.data, self.args(ROOT, self.frames[ROOT].input))
                except ScenarioError as error:
                    problems.append(f"context migration failed: {error.problem.detail}")
        plan.context = context
        present = set(context) if isinstance(context, dict) else set()
        missing = tuple(f"$context.{key}" for key in graph.context_keys if key not in present)
        plan.report = SwapReport(
            applicable=not problems,
            compatible=not problems and not plan.restarts,
            from_ref=self.scenario.ref,
            to_ref=scenario.ref,
            cursor=tuple(moves),
            operations=tuple(operations),
            restarts=tuple(target for _, target in plan.restarts),
            missing_variables=missing,
            problems=tuple(problems),
        )
        return plan

    def control_swap_version(self, action) -> None:
        if self.status not in RECOVERABLE:
            raise Reject("invalid_status", f"not allowed while the fabula is {self.status}")
        if self.agenda:
            raise Reject("not_at_rest", "the fabula has pending work; swap versions at a safe point")
        plan = self.plan_swap(action.scenario, action.mapping, action.context_migration)
        if not plan.report.applicable:
            raise Reject("incompatible", "; ".join(plan.report.problems), report=plan.report)
        self.apply_swap(action.scenario, plan)

    def apply_swap(self, scenario: ScenarioSnapshot, plan: SwapPlan) -> None:
        for op_id in plan.cancelled_ops:
            self.close_op(op_id)
        previous_ref = self.scenario.ref
        old_frames = self.frames
        frames = {}
        for path, frame in old_frames.items():
            if path in plan.targets:
                frames[plan.targets[path]] = self.remap_frame(frame, plan)
            elif frame.failed is None:
                self.observe(NodeCancelled.model_construct(at=self.at, node=path, visit=frame.visit, attempt=frame.attempt, reason="version swap"))
        self.inflight = {op_id: op.model_copy(update={"owner": plan.targets[op.owner]}) for op_id, op in self.inflight.items()}
        for old, new in [*plan.targets.items(), *plan.restarts]:
            if old in self.visits:
                self.visits[new] = max(self.visits.get(new, 0), self.visits[old])
        self.frames = frames
        self.scenario = scenario
        self.graph = plan.graph
        self.data = plan.context
        self._workflow_arg = None
        self.observe(ScenarioSwapped(at=self.at, previous_ref=previous_ref, ref=scenario.ref, report=plan.report))
        for old, new in plan.restarts:
            raw_input = old_frames[old].raw_input
            # Restarting a node of a failed fabula brings the fabula back, like RetryTask.
            self.recover(new)
            parent = plan.graph.nodes[new].parent
            if self.frames[parent].kind == "fork":
                self.push(Enter.model_construct(path=new, raw_input=raw_input, forced=True))
            else:
                self.enter_child(parent, new, raw_input, forced=True)

    def remap_frame(self, frame, plan: SwapPlan):
        changes: dict[str, Any] = {"path": plan.targets[frame.path]}
        restarted = dict(plan.restarts)
        current = getattr(frame, "current", None)
        if current is not None:
            changes["current"] = plan.targets.get(current) or restarted.get(current)
        if frame.kind == "fork":
            changes["branches"] = tuple(
                branch.model_copy(update={"path": plan.targets.get(branch.path) or restarted.get(branch.path) or plan.mapping.get(branch.path, branch.path)})
                for branch in frame.branches
            )
        return frame.model_copy(update=changes)
