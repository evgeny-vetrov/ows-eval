import re
from typing import Literal

from pydantic import field_validator

from ai.gena.services.fabula.engine.model.base import Frozen

ActorKind = Literal["user", "service", "agent"]

# OWS names: lowercase hostname-like tokens.
_NAME = re.compile(r"^[a-z0-9](-*[a-z0-9])*$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127}$")


class Actor(Frozen):
    """Who asks. The transport authenticates it; the `Authorizer` port decides rights."""

    id: str
    kind: ActorKind = "user"

    def __str__(self) -> str:
        return f"{self.kind}:{self.id}"


def scenario_id(namespace: str, name: str) -> str:
    return f"{namespace}/{name}"


def version_ref(scenario: str, version: str) -> str:
    """The engine's scenario ref: `namespace/name:version`."""
    return f"{scenario}:{version}"


def split_ref(ref: str) -> tuple[str, str]:
    scenario, _, version = ref.rpartition(":")
    return scenario, version


def is_scenario_id(value: str) -> bool:
    namespace, slash, name = value.partition("/")
    return bool(slash) and bool(_NAME.match(namespace)) and bool(_NAME.match(name))


def check_request_id(value: str) -> str:
    if not _REQUEST_ID.match(value):
        raise ValueError("request_id must be 1-128 characters of letters, digits, '_', '.', ':' or '-'")
    return value


class RequestBase(Frozen):
    """Every mutation carries a caller-chosen `request_id`: repeating it is harmless."""

    request_id: str

    @field_validator("request_id")
    @classmethod
    def _request_id(cls, value: str) -> str:
        return check_request_id(value)
