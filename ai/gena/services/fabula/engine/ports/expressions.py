"""Expression evaluation port (pure).

Contract:
* `compile(text)` never raises for bad input: it returns `ExpressionDiagnostics`.
  Texts longer than the engine's limit are rejected here.
* `evaluate(compiled, input, args)` is deterministic and side-effect free. `input` is
  the value of `.`; `args` maps variable names without `$` (`context`, `input`,
  `output`, `task`, `workflow`, `runtime`, loop and error variables) to JSON values.
* Evaluation failures of the expression itself (type errors, missing functions,
  exceeded deterministic limits) raise `ExpressionError`; they become `expression`
  errors of the scenario and can be caught with `try`.
* Limits: expression length, input size and evaluation time. Time must be measured
  deterministically (steps, fuel). An adapter that can only enforce a wall-clock
  budget raises `ExpressionResourceExhausted`; the core then aborts the transition
  with `CoreError` instead of recording a non-reproducible outcome.
* `CompiledExpression.variables` lists referenced variable paths such as
  `$context.ticket.id` (best effort; may be empty when unknown). It is used by the
  version-swap report to name context keys a new version needs.
"""

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from ai.gena.services.fabula.engine.model.base import Frozen


class ExpressionError(Exception):
    pass


class ExpressionResourceExhausted(Exception):
    """A non-deterministic limit was hit; the transition must not be committed."""


class ExpressionDiagnostics(Frozen):
    text: str
    messages: tuple[str, ...]
    offset: int | None = None


@runtime_checkable
class CompiledExpression(Protocol):
    text: str
    variables: tuple[str, ...]


class ExpressionEngine(Protocol):
    def compile(self, text: str) -> CompiledExpression | ExpressionDiagnostics: ...

    def evaluate(self, compiled: CompiledExpression, input: Any, args: Mapping[str, Any]) -> Any: ...


def is_expression(value: Any) -> bool:
    """OWS strict mode: a runtime expression is a whole string wrapped in `${ }`."""
    return isinstance(value, str) and value.startswith("${") and value.endswith("}")


def expression_body(value: str) -> str:
    return value[2:-1].strip()
