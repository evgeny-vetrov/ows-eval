from pydantic import Field

from ai.gena.services.fabula.engine.model.base import Frozen
from ai.gena.services.fabula.engine.model.jsonvalue import Json


class ExecutionContext(Frozen):
    """Who runs the fabula and why. The core copies it into commands untouched."""

    run_as: str | None = None
    trigger: Json = None
    labels: dict[str, str] = Field(default_factory=dict)
