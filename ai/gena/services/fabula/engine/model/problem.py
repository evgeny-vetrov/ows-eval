"""RFC 7807 problem details plus the standard OWS error types."""

from typing import Any

from ai.gena.services.fabula.engine.model.base import Frozen

OWS_ERRORS = "https://serverlessworkflow.io/spec/1.0.0/errors/"


class ErrorType:
    CONFIGURATION = OWS_ERRORS + "configuration"
    VALIDATION = OWS_ERRORS + "validation"
    EXPRESSION = OWS_ERRORS + "expression"
    AUTHENTICATION = OWS_ERRORS + "authentication"
    AUTHORIZATION = OWS_ERRORS + "authorization"
    TIMEOUT = OWS_ERRORS + "timeout"
    COMMUNICATION = OWS_ERRORS + "communication"
    RUNTIME = OWS_ERRORS + "runtime"


DEFAULT_STATUS = {
    ErrorType.CONFIGURATION: 400,
    ErrorType.VALIDATION: 400,
    ErrorType.EXPRESSION: 400,
    ErrorType.AUTHENTICATION: 401,
    ErrorType.AUTHORIZATION: 403,
    ErrorType.TIMEOUT: 408,
    ErrorType.COMMUNICATION: 500,
    ErrorType.RUNTIME: 500,
}

PROBLEM_FIELDS = ("type", "status", "title", "detail", "instance")


class Problem(Frozen):
    type: str
    status: int
    title: str | None = None
    detail: str | None = None
    instance: str | None = None

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)

    @classmethod
    def of(cls, error_type: str, title: str, detail: str | None = None, instance: str | None = None) -> "Problem":
        return cls(type=error_type, status=DEFAULT_STATUS.get(error_type, 500), title=title, detail=detail, instance=instance)
