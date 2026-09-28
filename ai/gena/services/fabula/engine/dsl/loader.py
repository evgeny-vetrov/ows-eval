"""Public DSL entry points: parse + validate + pin descriptors, and compile a snapshot."""

import json
from dataclasses import dataclass
from typing import Any

from ai.gena.services.fabula.engine.dsl.compiler import Compiler, ProfileLimits
from ai.gena.services.fabula.engine.dsl.diagnostics import Diagnostic, InvalidScenario, has_errors
from ai.gena.services.fabula.engine.dsl.graph import Graph
from ai.gena.services.fabula.engine.dsl.parser import MAX_DOCUMENT_BYTES, SourceMap, parse_json_text, parse_source
from ai.gena.services.fabula.engine.model.scenario import ScenarioSnapshot
from ai.gena.services.fabula.engine.ports.catalogs import CapabilityCatalog, EventCatalog, PinnedCapabilities, PinnedEvents
from ai.gena.services.fabula.engine.ports.expressions import ExpressionEngine
from ai.gena.services.fabula.engine.ports.schemas import SchemaValidator
from ai.gena.services.fabula.engine.version import SCENARIO_SCHEMA_VERSION


@dataclass(frozen=True)
class LoadResult:
    snapshot: ScenarioSnapshot | None
    graph: Graph | None
    diagnostics: tuple[Diagnostic, ...]

    @property
    def ok(self) -> bool:
        return self.snapshot is not None


def load_scenario(
    text: str,
    *,
    capabilities: CapabilityCatalog,
    events: EventCatalog,
    expressions: ExpressionEngine,
    schemas: SchemaValidator,
    limits: ProfileLimits = ProfileLimits(),
) -> LoadResult:
    """Parse YAML or JSON, validate the profile and pin the catalog descriptors.

    Works offline: the catalogs are the only outside knowledge it needs.
    """
    parsed = parse_json_text(text) if text.lstrip().startswith("{") else parse_source(text)
    if parsed.data is None or has_errors(parsed.diagnostics):
        return LoadResult(None, None, tuple(parsed.diagnostics))
    compiler = Compiler(expressions=expressions, schemas=schemas, capabilities=capabilities, events=events, limits=limits)
    result = compiler.compile(parsed.data, parsed.source_map)
    diagnostics = tuple(parsed.diagnostics) + tuple(result.diagnostics)
    if has_errors(diagnostics) or result.workflow is None:
        return LoadResult(None, None, diagnostics)
    snapshot = ScenarioSnapshot.build(parsed.data, result.capabilities, result.events)
    graph = Graph(digest=snapshot.digest, workflow=result.workflow, nodes=result.nodes, context_keys=result.context_keys)
    return LoadResult(snapshot, graph, diagnostics)


def validate_scenario(text: str, **ports: Any) -> tuple[Diagnostic, ...]:
    return load_scenario(text, **ports).diagnostics


def compile_snapshot(
    snapshot: ScenarioSnapshot,
    *,
    expressions: ExpressionEngine,
    schemas: SchemaValidator,
    limits: ProfileLimits = ProfileLimits(),
) -> Graph:
    """Compile a pinned snapshot against its own descriptors. Raises `InvalidScenario`."""
    if snapshot.schema_version > SCENARIO_SCHEMA_VERSION:
        raise InvalidScenario([Diagnostic(code="snapshot.schema_version", message=f"snapshot schema {snapshot.schema_version} is newer than {SCENARIO_SCHEMA_VERSION}")])
    size = len(json.dumps(snapshot.document, ensure_ascii=False).encode("utf-8"))
    if size > MAX_DOCUMENT_BYTES:
        raise InvalidScenario([Diagnostic(code="limit.document_size", message=f"document is {size} bytes")])
    compiler = Compiler(
        expressions=expressions,
        schemas=schemas,
        capabilities=PinnedCapabilities(snapshot.capabilities),
        events=PinnedEvents(snapshot.events),
        limits=limits,
    )
    result = compiler.compile(snapshot.document, SourceMap())
    if has_errors(result.diagnostics) or result.workflow is None:
        raise InvalidScenario(result.diagnostics)
    return Graph(digest=snapshot.digest, workflow=result.workflow, nodes=result.nodes, context_keys=result.context_keys)
