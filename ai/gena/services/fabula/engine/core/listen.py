"""`listen`: one subscription per event filter, re-checked on every delivery."""

from typing import Any

from ai.gena.services.fabula.engine.core import ids
from ai.gena.services.fabula.engine.core.evaluation import ScenarioError, evaluate_expr, evaluate_template, truthy
from ai.gena.services.fabula.engine.core.flow import Flow
from ai.gena.services.fabula.engine.core.semantics import LISTEN_ALL_ORDER
from ai.gena.services.fabula.engine.core.state import Fail, Finish
from ai.gena.services.fabula.engine.dsl.graph import EventFilterSpec
from ai.gena.services.fabula.engine.model.commands import Subscribe
from ai.gena.services.fabula.engine.model.event import Event
from ai.gena.services.fabula.engine.model.filter import AllOf, Predicate
from ai.gena.services.fabula.engine.model.problem import ErrorType, Problem
from ai.gena.services.fabula.engine.model.stimuli import EventDelivered


def read_event(mode: str, event: Event) -> Any:
    return event.envelope() if mode == "envelope" else event.data


class Listening(Flow):
    def start_listen(self, node, frame, item) -> None:
        strategy = node.listen.strategy
        args = self.args(node.path, frame.input)
        expires_at = self.subscription_expiry(node.path)
        main = [
            self.subscription(ids.op_id(self.fabula_id, node.path, frame.visit, frame.attempt, f"sub.{i}"), spec, frame.input, args, frame.since, expires_at)
            for i, spec in enumerate(strategy.filters)
        ]
        until_filters = strategy.until.filters if strategy.until else ()
        until = [
            self.subscription(ids.op_id(self.fabula_id, node.path, frame.visit, frame.attempt, f"until.{i}"), spec, frame.input, args, frame.since, expires_at)
            for i, spec in enumerate(until_filters)
        ]
        for index, command in enumerate(main):
            self.start_subscription(command, "sub", node.path, index)
        for index, command in enumerate(until):
            self.start_subscription(command, "until", node.path, index)
        self.update(
            node.path,
            main_subs=tuple(c.subscription_id for c in main),
            until_subs=tuple(c.subscription_id for c in until),
            until_hits=(False,) * len(until),
            satisfied=(False,) * len(main),
            by_filter=(None,) * len(main),
            acc=frame.input,
        )

    def subscription(self, subscription_id: str, spec: EventFilterSpec, input: Any, args: dict, since, expires_at) -> Subscribe:
        predicates = [Predicate(attr=attr, op="eq", value=evaluate_template(self.expressions, template, input, args)) for attr, template in spec.predicates]
        predicates += [
            Predicate(attr=rule.attr, op="eq", value=evaluate_template(self.expressions, rule.expect, input, args)) for rule in spec.correlations
        ]
        event_filter = predicates[0] if len(predicates) == 1 else AllOf(filters=tuple(predicates))
        return Subscribe(
            subscription_id=subscription_id,
            event_types=(spec.event_type,),
            filter=event_filter,
            since=since,
            expires_at=expires_at,
            context=self.state0.execution_context,
        )

    def subscription_expiry(self, path: str):
        deadlines = []
        for scope in [*self.graph.ancestors(path), path]:
            frame = self.frames.get(scope)
            op = self.inflight.get(frame.timeout_op) if frame is not None and frame.timeout_op else None
            if op is not None:
                deadlines.append(op.fire_at)
        deadlines += [op.fire_at for op in self.inflight.values() if op.role == "workflow_timeout"]
        return min(deadlines) + self.limits.subscription_expiry_margin if deadlines else None

    # --- deliveries ------------------------------------------------------------------

    def on_event(self, stimulus: EventDelivered) -> None:
        op = self.inflight.get(stimulus.subscription_id)
        if op is None or op.op != "subscription":
            self.ignore(stimulus, "late", "subscription is not active")
            return
        if stimulus.delivery_key in op.delivery_keys:
            self.ignore(stimulus, "duplicate", f"delivery {stimulus.delivery_key} was already consumed")
            return
        keys = (*op.delivery_keys, stimulus.delivery_key)[-self.limits.max_delivery_keys :]
        self.inflight[op.op_id] = op.model_copy(update={"delivery_keys": keys})
        subscription = op.subscription
        if stimulus.event.type not in subscription.event_types or not self.matcher.matches(subscription.filter, stimulus.event):
            self.ignore(stimulus, "filter_mismatch", "the event does not satisfy the subscription filter")
            return
        read = read_event(self.graph.nodes[op.owner].listen.read, stimulus.event)
        try:
            if op.role == "until":
                self.until_event(op.owner, op.index)
            else:
                self.main_event(op.owner, op.index, read)
        except ScenarioError as error:
            self.fail(Fail.model_construct(path=op.owner, problem=error.problem))

    def main_event(self, path: str, index: int, read: Any) -> None:
        frame = self.frames[path]
        strategy = self.graph.nodes[path].listen.strategy
        if strategy.mode == "all":
            self.close_op(frame.main_subs[index])
            frame = self.update(
                path,
                main_subs=_replace(frame.main_subs, index, None),
                satisfied=_replace(frame.satisfied, index, True),
                by_filter=_replace(frame.by_filter, index, read),
            )
            self.consume(path, read, complete=all(frame.satisfied), check_until=False)
        elif strategy.until_expr is None and strategy.until is None:
            self.close_listen_subscriptions(path)
            self.consume(path, read, complete=True, check_until=False)
        else:
            self.consume(path, read, complete=False, check_until=True)

    def until_event(self, path: str, index: int) -> None:
        frame = self.frames[path]
        self.close_op(frame.until_subs[index])
        hits = _replace(frame.until_hits, index, True)
        frame = self.update(path, until_subs=_replace(frame.until_subs, index, None), until_hits=hits)
        if self.graph.nodes[path].listen.strategy.until.mode == "all" and not all(hits):
            return
        self.close_listen_subscriptions(path)
        if frame.processing:
            self.update(path, closing=True)
        else:
            self.complete_listen(path)

    def close_listen_subscriptions(self, path: str) -> None:
        frame = self.frames[path]
        for op_id in (*frame.main_subs, *frame.until_subs):
            if op_id is not None:
                self.close_op(op_id)
        self.update(path, main_subs=(None,) * len(frame.main_subs), until_subs=(None,) * len(frame.until_subs))

    def consume(self, path: str, read: Any, complete: bool, check_until: bool) -> None:
        frame = self.frames[path]
        spec = self.graph.nodes[path].listen
        if len(frame.events) + len(frame.buffer) >= self.limits.max_listen_events:
            problem = Problem.of(ErrorType.RUNTIME, "Too many events", detail=f"listen accepted more than {self.limits.max_listen_events} events", instance=self.graph.nodes[path].pointer)
            self.fail(Fail.model_construct(path=path, problem=problem))
            return
        if spec.foreach:
            if complete:
                frame = self.update(path, closing=True)
            if frame.processing:
                self.update(path, buffer=(*frame.buffer, read))
            else:
                self.start_item(path, read)
            return
        frame = self.update(path, events=(*frame.events, read))
        if not complete and check_until and spec.strategy.until_expr is not None:
            complete = truthy(evaluate_expr(self.expressions, spec.strategy.until_expr, read, self.args(path, frame.input)))
        if complete:
            self.complete_listen(path)

    def complete_listen(self, path: str, extra: tuple = ()) -> None:
        frame = self.frames[path]
        spec = self.graph.nodes[path].listen
        if spec.foreach:
            output = frame.acc
        elif spec.strategy.mode == "all" and self.semantics.flag(LISTEN_ALL_ORDER) == "declaration":
            output = [event for event in frame.by_filter if event is not None] + list(extra)
        else:
            output = [*frame.events, *extra]
        self.finish(Finish.model_construct(path=path, raw_output=output))

    # --- foreach -------------------------------------------------------------------------

    def start_item(self, path: str, read: Any) -> None:
        frame = self.update(path, processing=True, item=read, current=None)
        self.enter_first(path, "foreach", frame.acc)

    def foreach_item_done(self, path: str, output: Any, exited: bool) -> None:
        frame = self.frames[path]
        spec = self.graph.nodes[path].listen
        try:
            args = self.args(path, frame.input, output=output, **{spec.item: frame.item, spec.at: frame.item_index})
            if spec.foreach_output_as is not None:
                output = evaluate_template(self.expressions, spec.foreach_output_as, output, args)
            if spec.foreach_export_as is not None:
                args["output"] = output
                self.data = evaluate_template(self.expressions, spec.foreach_export_as, output, args)
            stop = exited or (spec.strategy.until_expr is not None and truthy(evaluate_expr(self.expressions, spec.strategy.until_expr, frame.item, args)))
        except ScenarioError as error:
            self.fail(Fail.model_construct(path=path, problem=error.problem))
            return
        frame = self.update(path, acc=output, processing=False, item=None, item_index=frame.item_index + 1, current=None)
        if stop:
            self.complete_listen(path)
        elif frame.buffer:
            self.update(path, buffer=frame.buffer[1:])
            self.start_item(path, frame.buffer[0])
        elif frame.closing:
            self.complete_listen(path)


def _replace(values: tuple, index: int, value: Any) -> tuple:
    return (*values[:index], value, *values[index + 1 :])
