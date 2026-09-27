from typing import Literal

from ai.gena.services.fabula.engine.model.base import Frozen

FrameDisposition = Literal["kept", "restarted", "dropped", "unmapped"]
OpDisposition = Literal["kept", "cancelled"]


class CursorMove(Frozen):
    old_node: str
    new_node: str | None
    kind: str
    disposition: FrameDisposition
    reason: str = ""


class OperationFate(Frozen):
    op_id: str
    op_kind: str
    old_node: str
    disposition: OpDisposition


class SwapReport(Frozen):
    """What `SwapVersion` would do. `applicable` is false when the swap would be rejected."""

    applicable: bool
    compatible: bool
    from_ref: str
    to_ref: str
    cursor: tuple[CursorMove, ...] = ()
    operations: tuple[OperationFate, ...] = ()
    restarts: tuple[str, ...] = ()
    missing_variables: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()
