import re
from datetime import timedelta
from typing import Any, Literal

from ai.gena.services.fabula.engine.model.base import Frozen
from ai.gena.services.fabula.engine.model.problem import ErrorType

_REF = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9_.\-/]*):(?P<version>[A-Za-z0-9][A-Za-z0-9_.\-]*)@(?P<provider>[A-Za-z0-9][A-Za-z0-9_.\-]*)$"
)

Backoff = Literal["constant", "linear", "exponential"]


class CapabilityRef(Frozen):
    name: str
    version: str
    provider: str

    @classmethod
    def parse(cls, text: str) -> "CapabilityRef | None":
        match = _REF.match(text)
        return cls(**match.groupdict()) if match else None

    def __str__(self) -> str:
        return f"{self.name}:{self.version}@{self.provider}"


class RetryDefaults(Frozen):
    """Transparent retries the executor recommends for its capability."""

    max_retries: int
    delay: timedelta
    backoff: Backoff = "constant"
    jitter: timedelta = timedelta(0)
    retry_on: tuple[str, ...] = (ErrorType.COMMUNICATION,)


class CapabilityDescriptor(Frozen):
    ref: str
    title: str | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    effects: tuple[str, ...] = ()
    default_timeout: timedelta | None = None
    default_retry: RetryDefaults | None = None
    cancellable: bool = True


class EventTypeDescriptor(Frozen):
    """`data_schema` describes `event.data`, `attributes_schema` the extension attributes."""

    type: str
    data_schema: dict[str, Any] | None = None
    attributes_schema: dict[str, Any] | None = None
