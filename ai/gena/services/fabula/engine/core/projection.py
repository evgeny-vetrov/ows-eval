"""State projection for the API."""

from ai.gena.services.fabula.engine.core.run import is_strictly_under, waits_of
from ai.gena.services.fabula.engine.core.state import FabulaState
from ai.gena.services.fabula.engine.model.base import Frozen, UtcDatetime
from ai.gena.services.fabula.engine.model.jsonvalue import Json
from ai.gena.services.fabula.engine.model.observations import Status, WaitInfo
from ai.gena.services.fabula.engine.model.problem import Problem


class FabulaView(Frozen):
    fabula_id: str
    scenario_ref: str
    status: Status
    current_nodes: tuple[str, ...]
    waits: tuple[WaitInfo, ...]
    last_error: Problem | None = None
    failed_node: str | None = None
    output: Json = None
    started_at: UtcDatetime
    updated_at: UtcDatetime
    queued_stimuli: int = 0


def project(state: FabulaState) -> FabulaView:
    frames = [path for path in state.frames if path != "/"]
    leaves = tuple(path for path in frames if not any(is_strictly_under(other, path) for other in frames))
    return FabulaView(
        fabula_id=state.fabula_id,
        scenario_ref=state.scenario.ref,
        status=state.status,
        current_nodes=leaves,
        waits=waits_of(state.inflight),
        last_error=state.last_error,
        failed_node=state.failed_node,
        output=state.output,
        started_at=state.started_at,
        updated_at=state.last_at,
        queued_stimuli=len(state.paused_queue),
    )
