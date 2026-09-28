from typing import Any

from ai.gena.services.fabula.engine.dsl.graph import Expr, Template
from ai.gena.services.fabula.engine.model.problem import ErrorType, Problem
from ai.gena.services.fabula.engine.ports.expressions import ExpressionEngine, ExpressionError
from ai.gena.services.fabula.engine.ports.schemas import SchemaValidator


class ScenarioError(Exception):
    """An error of the scenario (not of the engine): it fails the current node."""

    def __init__(self, problem: Problem):
        super().__init__(problem.title)
        self.problem = problem


def truthy(value: Any) -> bool:
    return value is not None and value is not False


def evaluate_expr(engine: ExpressionEngine, expr: Expr, value: Any, args: dict[str, Any]) -> Any:
    try:
        return engine.evaluate(expr.compiled, value, args)
    except ExpressionError as exc:
        raise ScenarioError(
            Problem.of(ErrorType.EXPRESSION, "Expression evaluation failed", detail=f"{expr.text}: {exc}", instance=expr.pointer)
        ) from None


def evaluate_template(engine: ExpressionEngine, template: Template, value: Any, args: dict[str, Any]) -> Any:
    kind = template.kind
    if kind == "const":
        return template.value
    if kind == "expr":
        return evaluate_expr(engine, template.expr, value, args)
    if kind == "object":
        return {key: evaluate_template(engine, item, value, args) for key, item in template.items}
    return [evaluate_template(engine, item, value, args) for item in template.items]


def check_schema(schemas: SchemaValidator, schema: dict | None, value: Any, what: str, instance: str) -> None:
    if schema is None:
        return
    problems = schemas.validate(schema, value)
    if problems:
        raise ScenarioError(Problem.of(ErrorType.VALIDATION, f"{what} does not match its schema", detail="; ".join(problems), instance=instance))


def validation_problem(title: str, detail: str, instance: str) -> Problem:
    return Problem.of(ErrorType.VALIDATION, title, detail=detail, instance=instance)
