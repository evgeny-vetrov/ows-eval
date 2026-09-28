"""Errors of the management layer. The API maps each one to an RFC 7807 problem."""

from typing import Any


class ManagerError(Exception):
    status = 500
    code = "internal"

    def __init__(self, message: str, *, code: str | None = None, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.details = details or {}


class InvalidInput(ManagerError):
    status = 400
    code = "invalid_input"


class Forbidden(ManagerError):
    status = 403
    code = "forbidden"


class NotFound(ManagerError):
    status = 404
    code = "not_found"


class Conflict(ManagerError):
    """The request contradicts the current state (a version exists, a request id was reused...)."""

    status = 409
    code = "conflict"


class AlreadyExists(Conflict):
    code = "already_exists"


class RevisionConflict(ManagerError):
    """Optimistic locking failed: the caller's `expected_revision` is stale."""

    status = 412
    code = "revision_mismatch"


class Unprocessable(ManagerError):
    """The request is well formed but cannot be carried out as asked."""

    status = 422
    code = "unprocessable"
