from ai.gena.services.fabula.engine.dsl.parser import MAX_DOCUMENT_BYTES, parse_json_text, parse_source


def test_positions_are_recorded_per_pointer():
    parsed = parse_source("document:\n  name: x\ndo:\n  - first:\n      set: {a: 1}\n")
    assert parsed.source_map.locate("/document/name") == (2, 3)
    assert parsed.source_map.locate("/do/0/first/set") == (5, 7)
    assert parsed.source_map.locate("/do/0/first/set/a") == (5, 13)
    # Unknown pointers fall back to the closest known ancestor.
    assert parsed.source_map.locate("/do/0/first/set/zzz") == (5, 7)


def test_yaml_core_schema_keeps_json_meaning():
    parsed = parse_source("on: yes\nwhen: 2024-01-01\nflag: true\nnothing: null\nn: 010\nf: 1.5\n")
    assert parsed.data == {"on": "yes", "when": "2024-01-01", "flag": True, "nothing": None, "n": 8, "f": 1.5}


def test_duplicate_keys_are_reported():
    parsed = parse_source("a: 1\nb: 2\na: 3\n")
    [diagnostic] = parsed.diagnostics
    assert (diagnostic.code, diagnostic.pointer, diagnostic.line, diagnostic.column) == ("parse.duplicate_key", "/a", 3, 1)


def test_syntax_errors_carry_positions():
    parsed = parse_source("do:\n  - a: [1, 2\n")
    [diagnostic] = parsed.diagnostics
    assert diagnostic.code == "parse.syntax"
    assert diagnostic.line is not None and diagnostic.column is not None
    assert parsed.data is None


def test_json_is_parsed_strictly_with_positions():
    parsed = parse_json_text('{\n  "do": [\n    {"a": {"set": {"x": 1}}}\n  ]\n}')
    assert parsed.data == {"do": [{"a": {"set": {"x": 1}}}]}
    assert parsed.source_map.locate("/do/0/a/set") == (3, 12)
    broken = parse_json_text('{"a": 1,}')
    assert broken.diagnostics[0].code == "parse.syntax" and broken.diagnostics[0].line == 1


def test_document_size_limit():
    parsed = parse_source("a: '" + "x" * MAX_DOCUMENT_BYTES + "'\n")
    assert [d.code for d in parsed.diagnostics] == ["limit.document_size"]


def test_alias_bombs_are_bounded():
    bomb = "a: &a [x, x, x, x, x, x, x, x, x, x]\n"
    for level in "bcdefgh":
        previous = chr(ord(level) - 1)
        bomb += f"{level}: &{level} [*{previous}, *{previous}, *{previous}, *{previous}, *{previous}, *{previous}, *{previous}, *{previous}, *{previous}, *{previous}]\n"
    assert [d.code for d in parse_source(bomb).diagnostics] == ["limit.document_size"]
