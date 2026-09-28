from typing import Any

from pydantic import Field

from ai.gena.services.fabula.engine.model.base import Frozen, UtcDatetime
from ai.gena.services.fabula.engine.model.jsonvalue import Json


class Event(Frozen):
    """Neutral CloudEvents-like envelope. Extension attributes live in `attributes`."""

    id: str
    type: str
    source: str
    subject: str | None = None
    time: UtcDatetime | None = None
    data: Json = None
    attributes: dict[str, Json] = Field(default_factory=dict)

    def envelope(self) -> dict[str, Any]:
        """JSON form used by filters and by `listen.read: envelope`."""
        return self.model_dump(mode="json")
