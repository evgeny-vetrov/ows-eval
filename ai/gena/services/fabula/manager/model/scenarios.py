from typing import Literal

from pydantic import Field, field_validator

from ai.gena.services.fabula.engine.dsl.diagnostics import Diagnostic
from ai.gena.services.fabula.engine.model.base import Frozen, UtcDatetime
from ai.gena.services.fabula.engine.model.scenario import ScenarioSnapshot
from ai.gena.services.fabula.manager.model.common import RequestBase, is_scenario_id
from ai.gena.services.fabula.manager.model.resolution import ResolutionPolicy

VersionStatus = Literal["active", "deprecated"]


class ScenarioInfo(Frozen):
    """A business process: its identity, owners and the policy for migration conflicts."""

    scenario: str  # namespace/name
    title: str = ""
    description: str = ""
    owners: tuple[str, ...] = ()
    labels: dict[str, str] = Field(default_factory=dict)
    # Links to the product requirements the scenario implements.
    links: tuple[str, ...] = ()
    resolution_policy: ResolutionPolicy = ResolutionPolicy()
    created_at: UtcDatetime
    created_by: str
    updated_at: UtcDatetime
    revision: int = 1


class VersionSummary(Frozen):
    scenario: str
    version: str
    ref: str
    digest: str
    status: VersionStatus = "active"
    notes: str = ""
    published_at: UtcDatetime
    published_by: str
    deprecated_at: UtcDatetime | None = None
    deprecated_by: str | None = None


class ScenarioVersion(VersionSummary):
    """An immutable published version. Only `status` changes (deprecation)."""

    source: str
    snapshot: ScenarioSnapshot

    def summary(self) -> VersionSummary:
        return VersionSummary(**self.model_dump(exclude={"source", "snapshot"}))


# --- requests ------------------------------------------------------------------------


class ScenarioFields(Frozen):
    title: str = ""
    description: str = ""
    owners: tuple[str, ...] = ()
    labels: dict[str, str] = Field(default_factory=dict)
    links: tuple[str, ...] = ()


class CreateScenario(RequestBase, ScenarioFields):
    scenario: str

    @field_validator("scenario")
    @classmethod
    def _scenario(cls, value: str) -> str:
        if not is_scenario_id(value):
            raise ValueError("scenario must be 'namespace/name' with lowercase hostname-like parts")
        return value


class UpdateScenario(RequestBase):
    title: str | None = None
    description: str | None = None
    owners: tuple[str, ...] | None = None
    labels: dict[str, str] | None = None
    links: tuple[str, ...] | None = None
    expected_revision: int | None = None


class ValidateScenario(Frozen):
    source: str


class ValidationResult(Frozen):
    ok: bool
    ref: str | None = None
    digest: str | None = None
    diagnostics: tuple[Diagnostic, ...] = ()


class PublishVersion(RequestBase):
    """Publish the YAML or JSON document. Its header names the scenario and the version."""

    source: str
    notes: str = ""


class DeprecateVersion(RequestBase):
    reason: str = ""


class ScenarioList(Frozen):
    items: tuple[ScenarioInfo, ...]


class VersionList(Frozen):
    items: tuple[VersionSummary, ...]
