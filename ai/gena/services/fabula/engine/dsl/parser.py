"""YAML/JSON parsing that keeps source positions for every JSON pointer.

The loader follows the YAML 1.2 core schema: only `true/false` are booleans and dates
stay strings, so `on:` or `2024-01-01` mean what a JSON reader expects.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from ai.gena.services.fabula.engine.dsl.diagnostics import Diagnostic

MAX_DOCUMENT_BYTES = 256 * 1024
_MAX_VALUES = 200_000


class _CoreSchemaLoader(yaml.SafeLoader):
    pass


_CoreSchemaLoader.yaml_implicit_resolvers = {}
for _tag, _pattern, _first in (
    ("tag:yaml.org,2002:bool", r"^(?:true|True|TRUE|false|False|FALSE)$", "tTfF"),
    ("tag:yaml.org,2002:null", r"^(?:~|null|Null|NULL|)$", ["~", "n", "N", ""]),
    ("tag:yaml.org,2002:int", r"^(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$", "-+0123456789"),
    (
        "tag:yaml.org,2002:float",
        r"^(?:[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$",
        "-+0123456789.",
    ),
):
    _CoreSchemaLoader.add_implicit_resolver(_tag, re.compile(_pattern), list(_first))


def escape_pointer_token(token: str | int) -> str:
    return str(token).replace("~", "~0").replace("/", "~1")


@dataclass
class SourceMap:
    positions: dict[str, tuple[int, int]] = field(default_factory=dict)

    def locate(self, pointer: str) -> tuple[int | None, int | None]:
        while True:
            if pointer in self.positions:
                return self.positions[pointer]
            if not pointer:
                return None, None
            pointer = pointer.rsplit("/", 1)[0]


@dataclass
class Parsed:
    data: Any
    source_map: SourceMap
    diagnostics: list[Diagnostic]


class _Builder:
    def __init__(self, loader: _CoreSchemaLoader):
        self.loader = loader
        self.source_map = SourceMap()
        self.diagnostics: list[Diagnostic] = []
        self.values = 0

    def build(self, node: yaml.Node, pointer: str, mark: yaml.Mark | None = None) -> Any:
        self.values += 1
        if self.values > _MAX_VALUES:
            raise _TooLarge()
        start = mark or node.start_mark
        self.source_map.positions.setdefault(pointer, (start.line + 1, start.column + 1))
        if isinstance(node, yaml.MappingNode):
            result: dict[str, Any] = {}
            for key_node, value_node in node.value:
                key = self.loader.construct_object(key_node)
                if not isinstance(key, str):
                    key = str(key)
                child = f"{pointer}/{escape_pointer_token(key)}"
                if key in result:
                    self.diagnostics.append(
                        Diagnostic(
                            code="parse.duplicate_key",
                            message=f"duplicate key '{key}'",
                            pointer=child,
                            line=key_node.start_mark.line + 1,
                            column=key_node.start_mark.column + 1,
                        )
                    )
                result[key] = self.build(value_node, child, key_node.start_mark)
            return result
        if isinstance(node, yaml.SequenceNode):
            return [self.build(item, f"{pointer}/{index}") for index, item in enumerate(node.value)]
        value = self.loader.construct_object(node)
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            self.diagnostics.append(
                Diagnostic(code="parse.non_json_number", message="NaN and infinity are not JSON numbers", pointer=pointer, line=start.line + 1, column=start.column + 1)
            )
        return value


class _TooLarge(Exception):
    pass


def parse_source(text: str) -> Parsed:
    """Parse YAML or JSON text. Errors are reported as diagnostics, never raised."""
    size = len(text.encode("utf-8"))
    if size > MAX_DOCUMENT_BYTES:
        return Parsed(
            None,
            SourceMap(),
            [Diagnostic(code="limit.document_size", message=f"document is {size} bytes, the limit is {MAX_DOCUMENT_BYTES}")],
        )
    loader = _CoreSchemaLoader(text)
    try:
        node = loader.get_single_node()
        if node is None:
            return Parsed(None, SourceMap(), [Diagnostic(code="parse.empty", message="document is empty")])
        builder = _Builder(loader)
        data = builder.build(node, "")
        return Parsed(data, builder.source_map, builder.diagnostics)
    except _TooLarge:
        return Parsed(None, SourceMap(), [Diagnostic(code="limit.document_size", message="document expands to too many values")])
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark or exc.context_mark
        return Parsed(
            None,
            SourceMap(),
            [
                Diagnostic(
                    code="parse.syntax",
                    message=str(exc.problem or exc),
                    line=mark.line + 1 if mark else None,
                    column=mark.column + 1 if mark else None,
                )
            ],
        )
    except yaml.YAMLError as exc:
        return Parsed(None, SourceMap(), [Diagnostic(code="parse.syntax", message=str(exc))])
    finally:
        loader.dispose()


def parse_json_text(text: str) -> Parsed:
    """Strict JSON parsing; positions are recovered through the YAML composer."""
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        return Parsed(None, SourceMap(), [Diagnostic(code="parse.syntax", message=exc.msg, line=exc.lineno, column=exc.colno)])
    return parse_source(text)
