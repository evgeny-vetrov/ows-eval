"""Ready-made pure ports, catalogs and helpers for tests."""

import textwrap
from datetime import timedelta

from ai.gena.services.fabula.engine.dsl.loader import LoadResult, load_scenario
from ai.gena.services.fabula.engine.model.catalog import CapabilityDescriptor, EventTypeDescriptor, RetryDefaults
from ai.gena.services.fabula.engine.model.problem import ErrorType
from ai.gena.services.fabula.engine.model.scenario import ScenarioSnapshot
from ai.gena.services.fabula.engine.testing.catalogs import InMemoryCapabilityCatalog, InMemoryEventCatalog
from ai.gena.services.fabula.engine.testing.expressions import TestOnlyExpressionEngine
from ai.gena.services.fabula.engine.testing.matcher import FilterEventMatcher
from ai.gena.services.fabula.engine.testing.schemas import Draft7SchemaValidator

STANDARD_CAPABILITIES = [
    CapabilityDescriptor(ref="echo:1@test", effects=("none",)),
    CapabilityDescriptor(
        ref="strict:1@test",
        input_schema={"type": "object", "required": ["x"], "properties": {"x": {"type": "integer"}}},
        output_schema={"type": "object", "required": ["y"], "properties": {"y": {"type": "integer"}}},
    ),
    CapabilityDescriptor(ref="slow:1@test", default_timeout=timedelta(hours=1)),
    CapabilityDescriptor(
        ref="flaky:1@test",
        default_retry=RetryDefaults(max_retries=2, delay=timedelta(seconds=10), backoff="exponential", retry_on=(ErrorType.COMMUNICATION,)),
    ),
    CapabilityDescriptor(ref="fire:1@test", cancellable=False, default_timeout=timedelta(minutes=5)),
]

STANDARD_EVENTS = [
    EventTypeDescriptor(
        type="vcs.pr.merged",
        data_schema={
            "type": "object",
            "properties": {"pr_id": {"type": "string"}, "repo": {"type": "string"}, "author": {"type": "object", "properties": {"login": {"type": "string"}}}},
        },
        attributes_schema={"type": "object", "properties": {"tenant": {"type": "string"}}},
    ),
    EventTypeDescriptor(type="vcs.pr.closed", data_schema={"type": "object", "properties": {"pr_id": {"type": "string"}}}),
    EventTypeDescriptor(type="test.ping", data_schema={"type": "object", "additionalProperties": True}),
    EventTypeDescriptor(type="test.stop", data_schema={"type": "object", "additionalProperties": True}),
    EventTypeDescriptor(type="test.notice", data_schema={"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}}),
]

HEADER = """\
document:
  dsl: '1.0.3'
  namespace: test
  name: {name}
  version: '{version}'
"""


def scenario_text(body: str, name: str = "scenario", version: str = "1.0.0") -> str:
    """Prepend a document header to a YAML body written at column zero."""
    return HEADER.format(name=name, version=version) + textwrap.dedent(body).lstrip("\n")


class Kit:
    """Pure ports and catalogs wired together, as a host would do it."""

    def __init__(self, capabilities: list[CapabilityDescriptor] | None = None, events: list[EventTypeDescriptor] | None = None):
        self.expressions = TestOnlyExpressionEngine()
        self.matcher = FilterEventMatcher()
        self.schemas = Draft7SchemaValidator()
        self.capabilities = InMemoryCapabilityCatalog(STANDARD_CAPABILITIES + (capabilities or []))
        self.events = InMemoryEventCatalog(STANDARD_EVENTS + (events or []))

    def load(self, text: str) -> LoadResult:
        return load_scenario(text, capabilities=self.capabilities, events=self.events, expressions=self.expressions, schemas=self.schemas)

    def snapshot(self, text: str) -> ScenarioSnapshot:
        result = self.load(text)
        if not result.ok:
            raise AssertionError("scenario is invalid:\n" + "\n".join(str(d) for d in result.diagnostics))
        return result.snapshot
