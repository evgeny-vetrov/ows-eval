"""Interventions (`Control` stimuli).

Each action is checked against the current state and either applied as a whole or
rejected with a reason: handlers run against a checkpoint that is restored on
rejection. Closing commands are emitted at once; entering nodes goes through the
agenda, which does not run while the fabula is paused.
"""

from dataclasses import dataclass
from typing import Any

from ai.gena.services.fabula.engine.core import ids
from ai.gena.services.fabula.engine.core.evaluation import ScenarioError
from ai.gena.services.fabula.engine.core.listen import Listening, read_event
from ai.gena.services.fabula.engine.core.run import ROOT, TERMINAL, is_under
from ai.gena.services.fabula.engine.core.state import ControlRecord, Enter, Finish
from ai.gena.services.fabula.engine.model.commands import Cancelled, Subscribe
from ai.gena.services.fabula.engine.model.jsonvalue import merge_patch
from ai.gena.services.fabula.engine.model.observations import (
    ControlAccepted,
    ControlRejected,
    FabulaCancelled,
    NodeSkipped,
)
from ai.gena.services.fabula.engine.model.stimuli import Control
from ai.gena.services.fabula.engine.model.swap import SwapReport

RECOVERABLE = ("running", "waiting", "paused", "failed")
LIVE = ("running", "waiting", "paused")


class Reject(Exception):
    def __init__(self, code: str, message: str, report: SwapReport | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.report = report


@dataclass
class _Checkpoint:
    values: dict[str, Any]
    commands: int
    observations: int


_RESTORABLE = (
    "status", "frames", "inflight", "visits", "agenda", "paused_queue", "data", "output", "problem",
    "failed_node", "last_error", "yield_seq", "scenario", "graph",
)


class Controls(Listening):
    def on_control(self, stimulus: Control) -> None:
        previous = next((r for r in self.recent_controls if r.request_id == stimulus.request_id), None)
        if previous is not None:
            verdict = "accepted" if previous.accepted else f"rejected ({previous.code})"
            self.ignore(stimulus, "duplicate", f"request {stimulus.request_id} was already {verdict}")
            return
        action = stimulus.action
        checkpoint = self.checkpoint()
        try:
            try:
                getattr(self, f"control_{action.type}")(action)
            except ScenarioError as error:
                raise Reject("evaluation_failed", f"{error.problem.title}: {error.problem.detail}") from None
        except Reject as rejection:
            self.restore(checkpoint)
            self.observe(
                ControlRejected(
                    at=self.at,
                    request_id=stimulus.request_id,
                    actor=stimulus.actor,
                    reason=stimulus.reason,
                    action=action.type,
                    code=rejection.code,
                    message=rejection.message,
                    report=rejection.report,
                )
            )
            self.remember_control(ControlRecord(request_id=stimulus.request_id, accepted=False, code=rejection.code))
            return
        accepted = ControlAccepted(at=self.at, request_id=stimulus.request_id, actor=stimulus.actor, reason=stimulus.reason, action=action.type)
        self.observations.insert(checkpoint.observations, accepted)
        self.remember_control(ControlRecord(request_id=stimulus.request_id, accepted=True))

    def checkpoint(self) -> _Checkpoint:
        values = {name: getattr(self, name) for name in _RESTORABLE}
        for name in ("frames", "inflight", "visits"):
            values[name] = dict(values[name])
        values["agenda"] = type(self.agenda)(self.agenda)
        values["paused_queue"] = list(self.paused_queue)
        return _Checkpoint(values, len(self.commands), len(self.observations))

    def restore(self, checkpoint: _Checkpoint) -> None:
        for name, value in checkpoint.values.items():
            setattr(self, name, value)
        del self.commands[checkpoint.commands :]
        del self.observations[checkpoint.observations :]

    def remember_control(self, record: ControlRecord) -> None:
        self.recent_controls.append(record)
        overflow = len(self.recent_controls) - self.limits.max_recent_controls
        if overflow > 0:
            del self.recent_controls[:overflow]

    # --- checks ------------------------------------------------------------------------

    def require_status(self, allowed: tuple[str, ...]) -> None:
        if self.status not in allowed:
            raise Reject("invalid_status", f"not allowed while the fabula is {self.status}")

    def require_node(self, path: str):
        if path not in self.graph.nodes:
            raise Reject("unknown_node", f"'{path}' is not a node of {self.scenario.ref}")
        if path == ROOT:
            raise Reject("root_node", "the workflow itself is not a task")
        return self.graph.nodes[path]

    def require_frame(self, path: str):
        self.require_node(path)
        frame = self.frames.get(path)
        if frame is None:
            raise Reject("not_active", f"'{path}' is not being executed")
        if self.status == "failed" and frame.failed is None:
            raise Reject("not_on_failure_path", f"'{path}' is not on the path of the failure")
        return frame

    # --- recovery of a failed fabula -----------------------------------------------------

    def recover(self, path: str) -> None:
        """Bring a failed fabula back to life above `path` before re-entering `path`."""
        if self.status != "failed":
            return
        self.status = "running"
        self.problem = None
        self.failed_node = None
        timeout = self.graph.workflow.timeout
        if timeout is not None:
            self.arm_workflow_timeout(self.at + self.duration_of(timeout, ROOT, self.frames[ROOT].input))
        for scope in self.graph.ancestors(path):
            frame = self.frames.get(scope)
            if frame is None:
                continue
            frame = self.update(scope, failed=None)
            node = self.graph.nodes[scope]
            if node.timeout is not None and scope != ROOT:
                self.arm_timeout(scope)
            if frame.kind == "fork":
                for branch in frame.branches:
                    if branch.status in ("cancelled", "failed"):
                        self.set_branch(scope, branch.path, status="running")
                        if not is_under(path, branch.path):
                            self.push(Enter.model_construct(path=branch.path, raw_input=frame.input))
            elif frame.kind == "listen":
                self.renew_subscriptions(scope)

    def renew_subscriptions(self, path: str) -> None:
        frame = self.frames[path]
        strategy = self.graph.nodes[path].listen.strategy
        args = self.args(path, frame.input)
        expires_at = self.subscription_expiry(path)
        renewed = {}
        for role, ops, specs in (("sub", frame.main_subs, strategy.filters), ("until", frame.until_subs, strategy.until.filters if strategy.until else ())):
            fresh = []
            for index, op_id in enumerate(ops):
                if op_id is None:
                    fresh.append(None)
                    continue
                new_id = ids.next_generation(op_id)
                command = self.subscription(new_id, specs[index], frame.input, args, frame.since, expires_at)
                self.start_subscription(command, role, path, index)
                fresh.append(new_id)
            renewed["main_subs" if role == "sub" else "until_subs"] = tuple(fresh)
        self.update(path, **renewed)

    # --- actions -------------------------------------------------------------------------

    def control_pause(self, action) -> None:
        self.require_status(("running", "waiting"))
        self.status = "paused"

    def control_resume(self, action) -> None:
        self.require_status(("paused",))
        self.status = "running"
        self.run_agenda()
        queued, self.paused_queue = self.paused_queue, []
        for stimulus in queued:
            if self.status in TERMINAL:
                self.ignore(stimulus, "final", f"the fabula became {self.status} before this stimulus was applied")
                continue
            self.activate()
            self.route(stimulus)
            self.run_agenda()

    def control_cancel(self, action) -> None:
        self.require_status(RECOVERABLE)
        self.close_ops_under(ROOT)
        self.remove_frames_under(ROOT, "fabula cancelled", include_self=True)
        self.agenda.clear()
        for stimulus in self.paused_queue:
            self.ignore(stimulus, "final", "the fabula was cancelled")
        self.paused_queue = []
        self.status = "cancelled"
        self.command(Cancelled())
        self.observe(FabulaCancelled(at=self.at))

    def control_skip_task(self, action) -> None:
        self.require_status(RECOVERABLE)
        frame = self.require_frame(action.node)
        self.recover(action.node)
        self.close_ops_under(action.node)
        self.remove_frames_under(action.node, "parent skipped", include_self=False)
        self.drop_agenda_under(action.node, include_self=True)
        self.update(action.node, failed=None)
        self.observe(NodeSkipped.model_construct(at=self.at, node=action.node, visit=frame.visit, reason="control", output=action.output))
        self.push(Finish.model_construct(path=action.node, raw_output=action.output))

    def control_retry_task(self, action) -> None:
        self.require_status(RECOVERABLE)
        frame = self.require_frame(action.node)
        self.recover(action.node)
        self.close_ops_under(action.node)
        self.remove_frames_under(action.node, "retried", include_self=True)
        self.drop_agenda_under(action.node, include_self=True)
        self.push(Enter.model_construct(path=action.node, raw_input=frame.raw_input, visit=frame.visit, attempt=frame.attempt + 1, forced=True))

    def control_goto(self, action) -> None:
        self.require_status(RECOVERABLE)
        target = self.require_node(action.node)
        # Climb from the target to the nearest node that has a frame: the anchor.
        inactive: list[str] = []
        anchor = target.parent
        while anchor not in self.frames:
            inactive.insert(0, anchor)
            anchor = self.graph.nodes[anchor].parent
        chain = [*inactive, target.path]
        anchor_frame = self.frames[anchor]
        if anchor_frame.kind == "fork":
            raise Reject("fork_branch", "a goto cannot target a fork branch itself")
        if self.status == "failed" and anchor_frame.failed is None:
            raise Reject("not_on_failure_path", f"'{anchor}' is not on the path of the failure")
        self.require_executing(anchor, anchor_frame, self.graph.nodes[chain[0]].list_key)
        abandoned = anchor_frame.current
        if inactive:
            if not action.allow_scope_change:
                raise Reject("cross_scope", f"'{action.node}' is outside the active scope; set allow_scope_change to jump into it")
            for scope, below in zip(inactive, chain[1:]):
                kind = self.graph.nodes[scope].kind
                if not (kind == "do" or (kind == "try" and self.graph.nodes[below].list_key == "try")):
                    raise Reject("not_constructible", f"cannot jump into '{scope}': entering its body needs state a goto cannot provide")
        elif abandoned is not None and abandoned != target.path and self.is_composite(abandoned) and not action.allow_scope_change:
            raise Reject("cross_scope", f"leaving '{abandoned}' exits a nested scope; set allow_scope_change to allow it")
        abandoned_frame = self.frames.get(abandoned) if abandoned else None
        if action.input is not None:
            input = action.input.value
        elif abandoned_frame is not None:
            input = abandoned_frame.raw_input
        else:
            input = anchor_frame.input
        self.recover(chain[0])
        self.start_now()
        if abandoned is not None:
            self.drop(abandoned, "goto")
        self.enter_child(anchor, chain[0], input, descend=tuple(chain[1:]), forced=True)

    def start_now(self) -> None:
        """A goto while `schedule.after` is pending starts the workflow at once."""
        scheduled = [op_id for op_id, op in self.inflight.items() if op.role == "schedule"]
        if scheduled:
            self.close_op(scheduled[0])
            self.arm_workflow_deadline()

    def require_executing(self, path: str, frame, list_key: str) -> None:
        phase_lists = {"try": ("try",), "catch": ("catch",)}
        if frame.kind == "try":
            allowed = phase_lists.get(frame.phase, ())
        elif frame.kind == "listen":
            allowed = ("foreach",) if frame.processing else ()
        elif frame.kind == "fork":
            allowed = ()
        else:
            allowed = ("do",)
        if list_key not in allowed:
            raise Reject("scope_not_executing", f"'{path}' is not executing its '{list_key}' list")

    def is_composite(self, path: str) -> bool:
        node = self.graph.nodes[path]
        return node.kind in ("do", "try", "for", "fork") or (node.kind == "listen" and node.listen.foreach)

    def control_patch_context(self, action) -> None:
        self.require_status(RECOVERABLE)
        self.data = merge_patch(self.data, action.patch)

    def control_complete_wait(self, action) -> None:
        self.require_status(LIVE)
        frame = self.require_frame(action.node)
        node = self.graph.nodes[action.node]
        if node.kind not in ("wait", "listen"):
            raise Reject("not_a_wait", f"'{action.node}' is a {node.kind} task, not a wait or listen")
        if (action.event is None) == (action.output is None) and not (node.kind == "wait" and action.event is None):
            raise Reject("invalid_payload", "give exactly one of event or output")
        if node.kind == "wait":
            if action.event is not None:
                raise Reject("invalid_payload", "a wait task is completed with an output, not an event")
            output = action.output.value if action.output is not None else frame.input
        elif action.output is not None:
            output = action.output.value
        elif node.listen.foreach:
            raise Reject("invalid_payload", "a listen with foreach is completed with an output")
        else:
            output = [*frame.events, read_event(node.listen.read, action.event)]
        self.close_ops_under(action.node)
        self.remove_frames_under(action.node, "wait completed manually", include_self=False)
        self.drop_agenda_under(action.node, include_self=False)
        self.push(Finish.model_construct(path=action.node, raw_output=output))

    def control_extend_deadline(self, action) -> None:
        self.require_status(LIVE)
        fire_at = action.fire_at
        if fire_at < self.at:
            raise Reject("in_past", "the new deadline is in the past")
        if fire_at - self.at > self.limits.max_duration:
            raise Reject("too_far", f"the new deadline is more than {self.limits.max_duration.days} days away")
        if action.node == ROOT:
            current = [op for op in self.inflight.values() if op.role == "workflow_timeout"]
            if not current:
                raise Reject("no_deadline", "the workflow has no active deadline")
            self.arm_workflow_timeout(fire_at)
            self.refresh_subscription_expiry(ROOT)
            return
        frame = self.require_frame(action.node)
        if frame.kind == "wait" and frame.timer_op is not None:
            new_id = ids.next_generation(frame.timer_op)
            self.close_op(frame.timer_op)
            self.start_timer(new_id, "wait", action.node, fire_at)
            self.update(action.node, timer_op=new_id, fire_at=fire_at)
        elif frame.timeout_op is not None:
            new_id = ids.next_generation(frame.timeout_op)
            self.close_op(frame.timeout_op)
            self.start_timer(new_id, "timeout", action.node, fire_at)
            self.update(action.node, timeout_op=new_id)
            self.refresh_subscription_expiry(action.node)
        else:
            raise Reject("no_deadline", f"'{action.node}' has no deadline to move")

    def refresh_subscription_expiry(self, scope: str) -> None:
        for op_id, op in list(self.inflight.items()):
            if op.op != "subscription" or not is_under(op.owner, scope):
                continue
            expires_at = self.subscription_expiry(op.owner)
            if expires_at == op.subscription.expires_at:
                continue
            command = op.subscription.model_copy(update={"expires_at": expires_at})
            self.inflight[op_id] = op.model_copy(update={"subscription": command})
            self.command(Subscribe.model_validate(command.model_dump()))
