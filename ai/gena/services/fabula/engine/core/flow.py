"""Control flow: entering, finishing and failing nodes, and every task kind but `listen`."""

from datetime import timedelta
from typing import Any

from ai.gena.services.fabula.engine.core import ids
from ai.gena.services.fabula.engine.core.backoff import retry_delay
from ai.gena.services.fabula.engine.core.evaluation import (
    ScenarioError,
    check_schema,
    evaluate_expr,
    evaluate_template,
    truthy,
    validation_problem,
)
from ai.gena.services.fabula.engine.core.run import ROOT, RunBase
from ai.gena.services.fabula.engine.core.state import (
    Branch,
    CallFrame,
    DoFrame,
    Enter,
    Fail,
    Finish,
    ForFrame,
    ForkFrame,
    LeafFrame,
    ListenFrame,
    TryFrame,
    WaitFrame,
    WorkflowFrame,
)
from ai.gena.services.fabula.engine.dsl.graph import Timeout, child_path
from ai.gena.services.fabula.engine.model.commands import Completed, Failed, InvokeCapability, PublishEvent
from ai.gena.services.fabula.engine.model.durations import DurationError, format_duration, parse_duration, parse_instant
from ai.gena.services.fabula.engine.model.event import Event
from ai.gena.services.fabula.engine.model.jsonvalue import json_equal
from ai.gena.services.fabula.engine.model.observations import (
    FabulaCompleted,
    FabulaFailed,
    NodeCompleted,
    NodeFailed,
    NodeSkipped,
    NodeStarted,
    RetryScheduled,
)
from ai.gena.services.fabula.engine.model.problem import PROBLEM_FIELDS, ErrorType, Problem

FRAMES = {
    "do": DoFrame,
    "set": LeafFrame,
    "switch": LeafFrame,
    "raise": LeafFrame,
    "emit": LeafFrame,
    "wait": WaitFrame,
    "call": CallFrame,
    "try": TryFrame,
    "for": ForFrame,
    "fork": ForkFrame,
    "listen": ListenFrame,
}


def timeout_problem(path: str, pointer: str, detail: str) -> Problem:
    return Problem.of(ErrorType.TIMEOUT, "Task timed out", detail=f"'{path}' {detail}", instance=pointer)


class Flow(RunBase):
    def dispatch(self, item) -> None:
        if item.item == "enter":
            self.enter(item)
        elif item.item == "finish":
            self.finish(item)
        else:
            self.fail(item)

    # --- workflow ---------------------------------------------------------------------

    def begin(self, input: Any) -> None:
        workflow = self.graph.workflow
        self.visits[ROOT] = 1
        self.put(WorkflowFrame.model_construct(path=ROOT, visit=1, raw_input=input, input=input, started_at=self.at))
        try:
            check_schema(self.schemas, workflow.input_schema, input, "workflow input", "/input")
            transformed = evaluate_template(self.expressions, workflow.input_from, input, self.args(ROOT, input)) if workflow.input_from else input
        except ScenarioError as error:
            self.fail_workflow(error.problem, None)
            return
        self.update(ROOT, input=transformed)
        if workflow.schedule_after is not None:
            self.start_timer(ids.workflow_op_id(self.fabula_id, "schedule"), "schedule", ROOT, self.at + workflow.schedule_after)
            return
        self.launch()

    def launch(self) -> None:
        try:
            self.arm_workflow_deadline()
        except ScenarioError as error:
            self.fail_workflow(error.problem, None)
            return
        self.enter_first(ROOT, "do", self.frames[ROOT].input)

    def arm_workflow_deadline(self) -> None:
        timeout = self.graph.workflow.timeout
        if timeout is not None:
            self.arm_workflow_timeout(self.at + self.duration_of(timeout, ROOT, self.frames[ROOT].input))

    def arm_workflow_timeout(self, fire_at) -> None:
        previous = self.frames[ROOT].timeout_op
        op_id = ids.next_generation(previous) if previous else ids.workflow_op_id(self.fabula_id, "timeout")
        if previous in self.inflight:
            self.close_op(previous)
        self.start_timer(op_id, "workflow_timeout", ROOT, fire_at)
        self.update(ROOT, timeout_op=op_id)

    def finish_workflow(self, output: Any) -> None:
        workflow = self.graph.workflow
        try:
            args = self.args(ROOT, self.frames[ROOT].input, output=output)
            if workflow.output_as is not None:
                output = evaluate_template(self.expressions, workflow.output_as, output, args)
            check_schema(self.schemas, workflow.output_schema, output, "workflow output", "/output")
        except ScenarioError as error:
            self.fail_workflow(error.problem, None)
            return
        self.close_ops_under(ROOT)
        self.remove_frames_under(ROOT, "workflow completed", include_self=True)
        self.agenda.clear()
        self.status = "completed"
        self.output = output
        self.command(Completed.model_construct(output=output))
        self.observe(FabulaCompleted.model_construct(at=self.at, output=output))

    def end_workflow(self, output: Any) -> None:
        self.clear_inside(ROOT, "workflow ended")
        self.finish_workflow(output)

    def fail_workflow(self, problem: Problem, node: str | None) -> None:
        self.close_ops_under(ROOT)
        # Only the chain of failed frames is kept: it lets RetryTask/SkipTask/Goto recover.
        self.remove_frames_under(ROOT, "workflow failed", include_self=False, keep_failed=True)
        if ROOT in self.frames:
            self.update(ROOT, failed=problem)
        self.agenda.clear()
        self.status = "failed"
        self.problem = problem
        self.failed_node = node
        self.last_error = problem
        self.command(Failed(problem=problem))
        self.observe(FabulaFailed(at=self.at, node=node, problem=problem))

    # --- generic node lifecycle --------------------------------------------------------

    def enter_child(self, parent: str, child: str, input: Any, **fields: Any) -> None:
        if self.frames[parent].kind != "fork":
            self.update(parent, current=child)
        self.push(Enter.model_construct(path=child, raw_input=input, **fields))

    def enter_first(self, path: str, list_key: str, input: Any, descend: tuple[str, ...] = ()) -> None:
        if descend:
            self.enter_child(path, descend[0], input, descend=descend[1:], forced=True)
            return
        children = self.graph.nodes[path].list(list_key)
        if children:
            self.enter_child(path, children[0], input)
        else:
            self.list_done(path, input, exited=False)

    def enter(self, item: Enter) -> None:
        path = item.path
        node = self.graph.nodes[path]
        visit = item.visit if item.visit is not None else self.next_visit(path)
        raw = item.raw_input
        fields = dict(path=path, visit=visit, attempt=item.attempt, raw_input=raw, input=raw, started_at=self.at)
        if node.kind == "try":
            fields["first_started_at"] = self.at
        elif node.kind == "listen":
            fields["since"] = self.at
        self.put(FRAMES[node.kind].model_construct(**fields))
        started = False
        try:
            if node.condition is not None and not item.forced:
                if not truthy(evaluate_expr(self.expressions, node.condition, raw, self.args(path, raw))):
                    del self.frames[path]
                    self.observe(NodeSkipped.model_construct(at=self.at, node=path, visit=visit, reason="condition", output=raw))
                    self.child_done(node.parent, path, raw, "continue")
                    return
            check_schema(self.schemas, node.input_schema, raw, "task input", node.pointer)
            transformed = evaluate_template(self.expressions, node.input_from, raw, self.args(path, raw)) if node.input_from else raw
            self.update(path, input=transformed)
            self.observe(NodeStarted.model_construct(at=self.at, node=path, task=node.kind, visit=visit, attempt=item.attempt, input=transformed))
            started = True
            self.arm_timeout(path)
            getattr(self, f"start_{node.kind}")(node, self.frames[path], item)
        except ScenarioError as error:
            if not started:
                self.observe(NodeStarted.model_construct(at=self.at, node=path, task=node.kind, visit=visit, attempt=item.attempt, input=raw))
            if path in self.frames:
                self.fail(Fail.model_construct(path=path, problem=error.problem))

    def finish(self, item: Finish) -> None:
        path = item.path
        frame = self.frames.get(path)
        if frame is None:
            return
        node = self.graph.nodes[path]
        raw = item.raw_output
        try:
            args = self.args(path, frame.input, output=raw)
            output = evaluate_template(self.expressions, node.output_as, raw, args) if node.output_as else raw
            check_schema(self.schemas, node.output_schema, output, "task output", node.pointer)
            if node.export_as is not None:
                args["output"] = output
                context = evaluate_template(self.expressions, node.export_as, output, args)
                check_schema(self.schemas, node.export_schema, context, "exported context", node.pointer)
                self.data = context
        except ScenarioError as error:
            self.fail(Fail.model_construct(path=path, problem=error.problem))
            return
        self.close_ops_under(path)
        self.remove_frames_under(path, "parent completed", include_self=False)
        self.drop_agenda_under(path, include_self=False)
        del self.frames[path]
        self.observe(NodeCompleted.model_construct(at=self.at, node=path, visit=frame.visit, attempt=frame.attempt, output=output))
        self.child_done(node.parent, path, output, item.then or node.then)

    def fail(self, item: Fail) -> None:
        path = item.path
        frame = self.frames.get(path)
        if frame is None:
            return
        node = self.graph.nodes[path]
        problem = item.problem if item.problem.instance is not None else item.problem.model_copy(update={"instance": node.pointer})
        self.close_ops_under(path)
        self.remove_frames_under(path, "failed", include_self=False, keep_failed=True)
        self.drop_agenda_under(path, include_self=True)
        self.update(path, failed=problem)
        self.observe(NodeFailed.model_construct(at=self.at, node=path, visit=frame.visit, attempt=frame.attempt, problem=problem))
        self.last_error = problem
        if node.parent is None:
            self.fail_workflow(problem, path)
            return
        self.child_failed(node.parent, path, problem)

    def child_done(self, parent: str, child: str, output: Any, then: str) -> None:
        frame = self.frames[parent]
        if frame.kind == "fork":
            self.branch_done(parent, child, output)
            return
        if then == "end":
            self.end_workflow(output)
            return
        if then == "exit":
            self.list_done(parent, output, exited=True)
            return
        list_key = self.graph.nodes[child].list_key
        siblings = self.graph.nodes[parent].list(list_key)
        if then == "continue":
            index = siblings.index(child) + 1
            following = siblings[index] if index < len(siblings) else None
        else:
            following = child_path(parent, list_key, then)
        if following is None:
            self.list_done(parent, output, exited=False)
        else:
            self.enter_child(parent, following, output)

    def list_done(self, path: str, output: Any, exited: bool) -> None:
        frame = self.frames[path]
        kind = frame.kind
        if kind == "workflow":
            self.finish_workflow(output)
        elif kind == "for" and not exited:
            self.update(path, acc=output, index=frame.index + 1, current=None)
            self.next_iteration(path)
        elif kind == "listen":
            self.foreach_item_done(path, output, exited)
        else:
            self.finish(Finish.model_construct(path=path, raw_output=output))

    def child_failed(self, parent: str, child: str, problem: Problem) -> None:
        frame = self.frames[parent]
        if frame.kind == "try" and frame.phase == "try":
            self.remove_frames_under(child, "handled by try", include_self=True)
            self.try_caught(parent, problem)
        elif frame.kind == "fork":
            self.branch_failed(parent, child, problem)
        elif frame.kind == "workflow":
            self.update(ROOT, failed=problem)
            self.fail_workflow(problem, child)
        else:
            self.fail(Fail.model_construct(path=parent, problem=problem))

    # --- deadlines -----------------------------------------------------------------------

    def duration_of(self, timeout: Timeout, path: str, input: Any) -> timedelta:
        if timeout.after is not None:
            return timeout.after
        node = self.graph.nodes[path]
        value = evaluate_template(self.expressions, timeout.expr, input, self.args(path, input))
        try:
            duration = parse_duration(value)
        except DurationError as exc:
            raise ScenarioError(validation_problem("Invalid duration", str(exc), node.pointer)) from None
        if duration > self.limits.max_duration:
            raise ScenarioError(validation_problem("Duration is too long", f"{format_duration(duration)} exceeds {self.limits.max_duration.days} days", node.pointer))
        return duration

    def arm_timeout(self, path: str) -> None:
        node = self.graph.nodes[path]
        frame = self.frames[path]
        if node.timeout is not None:
            duration = self.duration_of(node.timeout, path, frame.input)
        elif node.kind == "call" and node.capability.default_timeout is not None:
            duration = node.capability.default_timeout
        else:
            return
        # A re-armed deadline (recovery after a failure) never reuses the id of the old one.
        previous = frame.timeout_op
        op_id = ids.next_generation(previous) if previous else ids.op_id(self.fabula_id, path, frame.visit, frame.attempt, "timeout")
        if previous in self.inflight:
            self.close_op(previous)
        self.start_timer(op_id, "timeout", path, self.at + duration)
        self.update(path, timeout_op=op_id)

    def on_task_timeout(self, path: str) -> None:
        if path not in self.frames:
            return
        # The frame keeps the id of the fired deadline: a re-armed one takes the next generation.
        node = self.graph.nodes[path]
        self.fail(Fail.model_construct(path=path, problem=timeout_problem(path, node.pointer, "exceeded its deadline")))

    def on_workflow_timeout(self) -> None:
        self.fail_workflow(Problem.of(ErrorType.TIMEOUT, "Workflow timed out", detail="the workflow exceeded its deadline", instance="/timeout"), None)

    # --- simple tasks ----------------------------------------------------------------------

    def start_do(self, node, frame, item: Enter) -> None:
        self.enter_first(node.path, "do", frame.input, item.descend)

    def start_set(self, node, frame, item: Enter) -> None:
        value = evaluate_template(self.expressions, node.template, frame.input, self.args(node.path, frame.input))
        self.finish(Finish.model_construct(path=node.path, raw_output=value))

    def start_switch(self, node, frame, item: Enter) -> None:
        args = self.args(node.path, frame.input)
        chosen = next(
            (case.then for case in node.cases if case.when is not None and truthy(evaluate_expr(self.expressions, case.when, frame.input, args))),
            None,
        )
        if chosen is None:
            chosen = next((case.then for case in node.cases if case.when is None), "continue")
        self.finish(Finish.model_construct(path=node.path, raw_output=frame.input, then=chosen))

    def start_raise(self, node, frame, item: Enter) -> None:
        error = evaluate_template(self.expressions, node.template, frame.input, self.args(node.path, frame.input))
        problem = self.problem_from(error, node.pointer)
        self.fail(Fail.model_construct(path=node.path, problem=problem))

    def problem_from(self, error: Any, pointer: str) -> Problem:
        status = error.get("status") if isinstance(error, dict) else None
        if not isinstance(error, dict) or not isinstance(error.get("type"), str) or isinstance(status, bool) or not isinstance(status, int):
            raise ScenarioError(validation_problem("Invalid error", "raise.error needs a string 'type' and an integer 'status'", pointer))
        fields = {}
        for key in ("title", "detail", "instance"):
            value = error.get(key)
            if value is not None and not isinstance(value, str):
                raise ScenarioError(validation_problem("Invalid error", f"raise.error.{key} must be a string", pointer))
            fields[key] = value
        return Problem(type=error["type"], status=status, title=fields["title"], detail=fields["detail"], instance=fields["instance"] or pointer)

    def start_wait(self, node, frame, item: Enter) -> None:
        if node.wait_until is not None:
            value = evaluate_template(self.expressions, node.wait_until, frame.input, self.args(node.path, frame.input))
            try:
                fire_at = max(parse_instant(value), self.at)
            except DurationError as exc:
                raise ScenarioError(validation_problem("Invalid wait", str(exc), node.pointer)) from None
            if fire_at - self.at > self.limits.max_duration:
                raise ScenarioError(validation_problem("Wait is too long", f"wait until {value} exceeds {self.limits.max_duration.days} days", node.pointer))
        else:
            fire_at = self.at + self.duration_of(node.wait_for, node.path, frame.input)
        op_id = ids.op_id(self.fabula_id, node.path, frame.visit, frame.attempt, "wait")
        self.start_timer(op_id, "wait", node.path, fire_at)
        self.update(node.path, timer_op=op_id, fire_at=fire_at)

    def on_wait_fired(self, path: str) -> None:
        frame = self.update(path, timer_op=None)
        self.finish(Finish.model_construct(path=path, raw_output=frame.input))

    def start_emit(self, node, frame, item: Enter) -> None:
        properties = evaluate_template(self.expressions, node.template, frame.input, self.args(node.path, frame.input))
        emit_id = ids.op_id(self.fabula_id, node.path, frame.visit, frame.attempt, "emit")
        if not isinstance(properties, dict):
            raise ScenarioError(validation_problem("Invalid event", "emit.event.with must evaluate to an object", node.pointer))
        source = properties.get("source", f"fabula:{self.fabula_id}")
        subject = properties.get("subject")
        if not isinstance(source, str) or (subject is not None and not isinstance(subject, str)):
            raise ScenarioError(validation_problem("Invalid event", "event source and subject must be strings", node.pointer))
        data = properties.get("data")
        if node.emit_type is not None:
            check_schema(self.schemas, node.emit_type.data_schema, data, "event data", node.pointer)
        attributes = {k: v for k, v in properties.items() if k not in ("type", "source", "subject", "data", "id", "time")}
        event = Event(id=emit_id, type=properties["type"], source=source, subject=subject, time=self.at, data=data, attributes=attributes)
        self.command(PublishEvent(emit_id=emit_id, event=event))
        self.finish(Finish.model_construct(path=node.path, raw_output=frame.input))

    # --- call ----------------------------------------------------------------------------------

    def start_call(self, node, frame, item: Enter) -> None:
        arguments = evaluate_template(self.expressions, node.template, frame.input, self.args(node.path, frame.input))
        check_schema(self.schemas, node.capability.input_schema, arguments, "capability input", node.pointer)
        self.update(node.path, arguments=arguments)
        self.invoke(node.path)

    def invoke(self, path: str) -> None:
        node = self.graph.nodes[path]
        frame = self.frames[path]
        invocation_id = ids.op_id(self.fabula_id, path, frame.visit, frame.attempt, "call")
        deadline = self.inflight.get(frame.timeout_op) if frame.timeout_op else None
        command = InvokeCapability(
            invocation_id=invocation_id,
            capability_ref=node.capability.ref,
            input=frame.arguments,
            timeout=deadline.fire_at - self.at if deadline else None,
            context=self.state0.execution_context,
        )
        self.start_invocation(command, path, node.capability.cancellable)
        self.update(path, invocation_id=invocation_id)

    def on_invocation_result(self, path: str, outcome) -> None:
        node = self.graph.nodes[path]
        self.update(path, invocation_id=None)
        if outcome.status == "ok":
            try:
                check_schema(self.schemas, node.capability.output_schema, outcome.output, "capability output", node.pointer)
            except ScenarioError as error:
                self.fail(Fail.model_construct(path=path, problem=error.problem))
                return
            self.finish(Finish.model_construct(path=path, raw_output=outcome.output))
        elif outcome.status == "error":
            self.call_failed(path, outcome.problem)
        else:
            self.call_failed(path, Problem.of(ErrorType.RUNTIME, "Invocation cancelled by executor", instance=node.pointer))

    def call_failed(self, path: str, problem: Problem) -> None:
        node = self.graph.nodes[path]
        frame = self.frames[path]
        policy = node.capability.default_retry
        if policy is not None and problem.type in policy.retry_on and frame.retries < policy.max_retries:
            delay = retry_delay(
                base=policy.delay,
                backoff=policy.backoff,
                retry=frame.retries + 1,
                jitter_from=timedelta(0),
                jitter_to=policy.jitter,
                fabula_id=self.fabula_id,
                path=path,
                cap=self.limits.max_duration,
            )
            op_id = ids.op_id(self.fabula_id, path, frame.visit, frame.attempt, "retry")
            self.start_timer(op_id, "retry", path, self.at + delay)
            self.update(path, retry_timer=op_id)
            self.observe(RetryScheduled(at=self.at, node=path, visit=frame.visit, next_attempt=frame.attempt + 1, fire_at=self.at + delay, problem=problem))
            return
        self.fail(Fail.model_construct(path=path, problem=problem))

    def on_call_retry(self, path: str) -> None:
        frame = self.frames[path]
        frame = self.update(path, attempt=frame.attempt + 1, retries=frame.retries + 1, retry_timer=None)
        self.observe(NodeStarted.model_construct(at=self.at, node=path, task="call", visit=frame.visit, attempt=frame.attempt, input=frame.input))
        self.invoke(path)

    # --- try / catch / retry ------------------------------------------------------------------

    def start_try(self, node, frame, item: Enter) -> None:
        self.arm_attempt_timer(node.path)
        self.enter_first(node.path, "try", frame.input, item.descend)

    def arm_attempt_timer(self, path: str) -> None:
        node = self.graph.nodes[path]
        retry = node.catch.retry if node.catch else None
        if retry is None or retry.limit_attempt_duration is None:
            return
        frame = self.frames[path]
        op_id = ids.op_id(self.fabula_id, path, frame.visit, frame.attempt, "attempt-timeout")
        self.start_timer(op_id, "attempt_timeout", path, self.at + retry.limit_attempt_duration)
        self.update(path, attempt_timer=op_id)

    def on_attempt_timeout(self, path: str) -> None:
        self.update(path, attempt_timer=None)
        node = self.graph.nodes[path]
        self.clear_inside(path, "attempt timed out")
        self.update(path, current=None)
        self.try_caught(path, timeout_problem(path, node.pointer, "attempt exceeded retry.limit.attempt.duration"))

    def try_caught(self, path: str, problem: Problem) -> None:
        frame = self.frames[path]
        if frame.attempt_timer is not None:
            self.close_op(frame.attempt_timer)
            frame = self.update(path, attempt_timer=None, current=None)
        node = self.graph.nodes[path]
        catch = node.catch
        variables = {catch.as_name: problem.to_json()}
        try:
            caught = all(json_equal(getattr(problem, key), value) for key, value in catch.errors_with.items() if key in PROBLEM_FIELDS)
            args = self.args(path, frame.input, **variables)
            if caught and catch.when is not None:
                caught = truthy(evaluate_expr(self.expressions, catch.when, frame.input, args))
            if caught and catch.except_when is not None:
                caught = not truthy(evaluate_expr(self.expressions, catch.except_when, frame.input, args))
            if caught and catch.retry is not None and self.schedule_retry(path, problem, args):
                return
        except ScenarioError as error:
            self.fail(Fail.model_construct(path=path, problem=error.problem))
            return
        # A catch that only retries does not swallow the error once retries are over.
        if not caught or (catch.retry is not None and not node.list("catch")):
            self.fail(Fail.model_construct(path=path, problem=problem))
            return
        if node.list("catch"):
            self.update(path, phase="catch", error=problem)
            self.enter_first(path, "catch", frame.input)
        else:
            self.finish(Finish.model_construct(path=path, raw_output=frame.input))

    def schedule_retry(self, path: str, problem: Problem, args: dict) -> bool:
        frame = self.frames[path]
        retry = self.graph.nodes[path].catch.retry
        number = frame.retries + 1
        if retry.limit_count is not None and number > retry.limit_count:
            return False
        if retry.when is not None and not truthy(evaluate_expr(self.expressions, retry.when, frame.input, args)):
            return False
        if retry.except_when is not None and truthy(evaluate_expr(self.expressions, retry.except_when, frame.input, args)):
            return False
        delay = retry_delay(
            base=retry.delay,
            backoff=retry.backoff,
            retry=number,
            jitter_from=retry.jitter_from,
            jitter_to=retry.jitter_to,
            fabula_id=self.fabula_id,
            path=path,
            cap=self.limits.max_duration,
        )
        fire_at = self.at + delay
        if retry.limit_duration is not None and fire_at - frame.first_started_at > retry.limit_duration:
            return False
        op_id = ids.op_id(self.fabula_id, path, frame.visit, frame.attempt, "retry")
        self.start_timer(op_id, "retry", path, fire_at)
        self.update(path, phase="retry_wait", error=problem, retry_timer=op_id, current=None)
        self.observe(RetryScheduled(at=self.at, node=path, visit=frame.visit, next_attempt=frame.attempt + 1, fire_at=fire_at, problem=problem))
        return True

    def on_try_retry(self, path: str) -> None:
        frame = self.frames[path]
        frame = self.update(path, phase="try", attempt=frame.attempt + 1, retries=frame.retries + 1, retry_timer=None, error=None)
        self.observe(NodeStarted.model_construct(at=self.at, node=path, task="try", visit=frame.visit, attempt=frame.attempt, input=frame.input))
        self.arm_attempt_timer(path)
        self.enter_first(path, "try", frame.input)

    # --- for -------------------------------------------------------------------------------------

    def start_for(self, node, frame, item: Enter) -> None:
        items = evaluate_expr(self.expressions, node.for_in, frame.input, self.args(node.path, frame.input))
        if items is None:
            items = []
        if not isinstance(items, list):
            raise ScenarioError(validation_problem("Invalid loop collection", "for.in must evaluate to an array", node.pointer))
        self.update(node.path, items=tuple(items), index=0, acc=frame.input)
        self.next_iteration(node.path)

    def next_iteration(self, path: str) -> None:
        frame = self.frames[path]
        node = self.graph.nodes[path]
        if frame.index >= len(frame.items):
            self.finish(Finish.model_construct(path=path, raw_output=frame.acc))
            return
        if frame.index >= self.limits.max_for_iterations:
            problem = Problem.of(ErrorType.RUNTIME, "Iteration limit exceeded", detail=f"more than {self.limits.max_for_iterations} iterations", instance=node.pointer)
            self.fail(Fail.model_construct(path=path, problem=problem))
            return
        if node.for_while is not None:
            try:
                keep_going = truthy(evaluate_expr(self.expressions, node.for_while, frame.acc, self.args(path, frame.input)))
            except ScenarioError as error:
                self.fail(Fail.model_construct(path=path, problem=error.problem))
                return
            if not keep_going:
                self.finish(Finish.model_construct(path=path, raw_output=frame.acc))
                return
        self.enter_first(path, "do", frame.acc)

    # --- fork ---------------------------------------------------------------------------------------

    def start_fork(self, node, frame, item: Enter) -> None:
        branches = tuple(Branch(path=branch) for branch in node.list("branches"))
        self.update(node.path, branches=branches)
        if not branches:
            self.finish(Finish.model_construct(path=node.path, raw_output=[]))
            return
        for branch in branches:
            self.push(Enter.model_construct(path=branch.path, raw_input=frame.input))

    def set_branch(self, fork: str, branch: str, **changes) -> ForkFrame:
        frame = self.frames[fork]
        branches = tuple(b.model_copy(update=changes) if b.path == branch else b for b in frame.branches)
        return self.update(fork, branches=branches)

    def cancel_branches(self, fork: str, reason: str) -> None:
        for branch in self.frames[fork].branches:
            if branch.status == "running":
                self.drop(branch.path, reason)
                self.set_branch(fork, branch.path, status="cancelled")

    def branch_done(self, fork: str, branch: str, output: Any) -> None:
        frame = self.set_branch(fork, branch, status="completed", output=output)
        if self.graph.nodes[fork].compete:
            self.cancel_branches(fork, "lost the race")
            self.finish(Finish.model_construct(path=fork, raw_output=output))
        elif all(b.status == "completed" for b in frame.branches):
            self.finish(Finish.model_construct(path=fork, raw_output=[b.output for b in frame.branches]))

    def branch_failed(self, fork: str, branch: str, problem: Problem) -> None:
        self.set_branch(fork, branch, status="failed")
        self.cancel_branches(fork, "sibling branch failed")
        self.fail(Fail.model_construct(path=fork, problem=problem))

    # --- timers ---------------------------------------------------------------------------------------

    def on_timer(self, op) -> None:
        role = op.role
        if role == "timeout":
            self.on_task_timeout(op.owner)
        elif role == "wait":
            self.on_wait_fired(op.owner)
        elif role == "retry":
            if self.frames[op.owner].kind == "try":
                self.on_try_retry(op.owner)
            else:
                self.on_call_retry(op.owner)
        elif role == "attempt_timeout":
            self.on_attempt_timeout(op.owner)
        elif role == "schedule":
            self.launch()
        elif role == "workflow_timeout":
            self.on_workflow_timeout()
