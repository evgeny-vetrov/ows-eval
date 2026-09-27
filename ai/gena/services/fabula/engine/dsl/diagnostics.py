from typing import Literal

from ai.gena.services.fabula.engine.model.base import Frozen

Severity = Literal["error", "warning"]


class Diagnostic(Frozen):
    code: str
    message: str
    severity: Severity = "error"
    pointer: str = ""
    node: str | None = None
    line: int | None = None
    column: int | None = None

    def __str__(self) -> str:
        where = f"{self.line}:{self.column}" if self.line is not None else self.pointer or "<document>"
        return f"{where} {self.code}: {self.message}"


def has_errors(diagnostics: list[Diagnostic] | tuple[Diagnostic, ...]) -> bool:
    return any(d.severity == "error" for d in diagnostics)


class InvalidScenario(Exception):
    def __init__(self, diagnostics: list[Diagnostic] | tuple[Diagnostic, ...]):
        self.diagnostics = tuple(diagnostics)
        super().__init__("; ".join(str(d) for d in self.diagnostics if d.severity == "error"))
