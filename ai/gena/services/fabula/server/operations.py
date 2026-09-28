"""Every operation the server dispatches: the manager's, the session's and, when the
sandbox runs, the sandbox's."""

from typing import Any

from ai.gena.services.fabula.manager.api.openapi import TAGS
from ai.gena.services.fabula.manager.api.operations import OPERATIONS, Operation
from ai.gena.services.fabula.server.sandbox.api import SANDBOX_OPERATIONS, SANDBOX_TAG
from ai.gena.services.fabula.server.session import SESSION_OPERATIONS, SESSION_TAG

SERVER_TAGS = [*TAGS, SESSION_TAG, SANDBOX_TAG]


def server_operations(sandbox: bool) -> tuple[Operation, ...]:
    return OPERATIONS + SESSION_OPERATIONS + (SANDBOX_OPERATIONS if sandbox else ())


class Facades:
    """One api object over several facades, as `Dispatcher` expects: the handler of
    each operation must live in exactly one of them."""

    def __init__(self, operations: tuple[Operation, ...], *facades: Any):
        self._handlers: dict[str, Any] = {}
        for op in operations:
            owners = [facade for facade in facades if callable(getattr(facade, op.handler, None))]
            if len(owners) != 1:
                raise ValueError(f"{len(owners)} facades handle {op.handler}")
            self._handlers[op.handler] = getattr(owners[0], op.handler)

    def __getattr__(self, name: str) -> Any:
        try:
            return self.__dict__["_handlers"][name]
        except KeyError:
            raise AttributeError(name) from None
