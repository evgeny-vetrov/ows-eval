"""The fabula runtime as the manager sees it.

`engine.runtime.host.Host` satisfies this protocol as is; a host on a durable execution
engine implements the same methods. Contract:

* `start` is idempotent by `fabula_id`.
* `control` and `swap_version` submit a `Control` stimulus with `request_id`; the core
  answers a repeated request id as a duplicate. The outcome shows up in the journal as
  `ControlAccepted` / `ControlRejected`.
* `state` / `view` read the stored state and raise
  `engine.ports.storage.FabulaNotFound` for an unknown id.
* `analyze_swap` is a pure preview over the stored state.
* `recover` finishes side effects of transitions that were saved but not acknowledged.
"""

from typing import Any, Protocol

from ai.gena.services.fabula.engine.core.projection import FabulaView
from ai.gena.services.fabula.engine.core.state import FabulaState
from ai.gena.services.fabula.engine.model.context import ExecutionContext
from ai.gena.services.fabula.engine.model.stimuli import ControlAction
from ai.gena.services.fabula.engine.model.swap import SwapReport


class FabulaRuntime(Protocol):
    async def start(self, fabula_id: str, scenario_ref: str, input: Any, context: ExecutionContext) -> None: ...

    async def control(self, fabula_id: str, request_id: str, actor: str, reason: str, action: ControlAction) -> None: ...

    async def analyze_swap(
        self, fabula_id: str, scenario_ref: str, mapping: dict[str, str] | None = None, context_migration: str | None = None
    ) -> SwapReport: ...

    async def swap_version(
        self,
        fabula_id: str,
        request_id: str,
        actor: str,
        reason: str,
        scenario_ref: str,
        mapping: dict[str, str] | None = None,
        context_migration: str | None = None,
    ) -> None: ...

    async def state(self, fabula_id: str) -> FabulaState: ...

    async def view(self, fabula_id: str) -> FabulaView: ...

    async def recover(self) -> None: ...
