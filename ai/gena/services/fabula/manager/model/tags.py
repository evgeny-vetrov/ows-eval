"""Tags: named pointers from a scenario to its versions (`trunk`, `canary`, ...).

A tag holds an allocation: one arm is a plain pointer, several arms split new starts
between versions (an A/B experiment). Tags only route new starts; running fabulas stay
on their version until a migration campaign moves them.
"""

import re
from typing import Literal

from pydantic import Field, model_validator

from ai.gena.services.fabula.engine.model.base import Frozen, UtcDatetime
from ai.gena.services.fabula.manager.model.common import RequestBase

TOTAL_WEIGHT = 10_000  # basis points
TRUNK = "trunk"
_TAG = re.compile(r"^[a-z0-9](-*[a-z0-9])*$")


def check_tag(value: str) -> str:
    if not _TAG.match(value) or len(value) > 63:
        raise ValueError("a tag is a lowercase hostname-like token of at most 63 characters")
    return value


class Arm(Frozen):
    arm: str
    version: str
    weight: int = Field(ge=0, le=TOTAL_WEIGHT)


class Allocation(Frozen):
    arms: tuple[Arm, ...]
    # Changing the salt reshuffles routing keys between arms (a new experiment).
    salt: str = ""

    @model_validator(mode="after")
    def _valid(self) -> "Allocation":
        if not self.arms:
            raise ValueError("an allocation needs at least one arm")
        names = [arm.arm for arm in self.arms]
        if len(set(names)) != len(names):
            raise ValueError("arm names must be unique")
        if sum(arm.weight for arm in self.arms) != TOTAL_WEIGHT:
            raise ValueError(f"arm weights must add up to {TOTAL_WEIGHT}")
        return self

    @classmethod
    def single(cls, version: str) -> "Allocation":
        return cls(arms=(Arm(arm="main", version=version, weight=TOTAL_WEIGHT),))

    @property
    def versions(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(arm.version for arm in self.arms if arm.weight))

    @property
    def single_version(self) -> str | None:
        versions = self.versions
        return versions[0] if len(versions) == 1 else None


TagChangeKind = Literal["create", "move", "allocate", "rollback"]


class TagState(Frozen):
    scenario: str
    tag: str
    allocation: Allocation
    revision: int = 1
    updated_at: UtcDatetime
    updated_by: str
    reason: str = ""


class TagChange(Frozen):
    """One entry of the append-only tag history."""

    scenario: str
    tag: str
    revision: int
    kind: TagChangeKind
    allocation: Allocation
    actor: str
    reason: str = ""
    at: UtcDatetime
    request_id: str = ""


class Assignment(Frozen):
    """How a start was routed. Recorded in the fabula's labels and in the index."""

    version: str
    ref: str
    tag: str | None = None
    tag_revision: int | None = None
    arm: str | None = None
    bucket: int | None = None


# --- requests ------------------------------------------------------------------------


class _TagRequest(RequestBase):
    reason: str = ""
    # None creates the tag; otherwise it must equal the tag's current revision.
    expected_revision: int | None = None


class MoveTag(_TagRequest):
    version: str
    # Ask about running fabulas: open a migration campaign awaiting a decision.
    propose_migration: bool = True


class SetAllocation(_TagRequest):
    allocation: Allocation


class RollbackTag(_TagRequest):
    """Point the tag back to an earlier revision's allocation or to a version."""

    to_revision: int | None = None
    to_version: str | None = None
    propose_migration: bool = True

    @model_validator(mode="after")
    def _one_target(self) -> "RollbackTag":
        if (self.to_revision is None) == (self.to_version is None):
            raise ValueError("give exactly one of to_revision and to_version")
        return self


class TagList(Frozen):
    items: tuple[TagState, ...]


class TagHistory(Frozen):
    items: tuple[TagChange, ...]


class TagMoveResult(Frozen):
    tag: TagState
    campaign_id: str | None = None
