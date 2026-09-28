import pytest

from ai.gena.services.fabula.engine.testing.kit import Kit, scenario_text

KIT = Kit()

OK_TASK = """\
do:
  - noop:
      set: {a: 1}
"""


def diagnostics(body: str):
    return KIT.load(scenario_text(body)).diagnostics


def codes(body: str) -> list[str]:
    return [d.code for d in diagnostics(body) if d.severity == "error"]


FORBIDDEN = [
    (
        "run",
        """\
        do:
          - shell:
              run:
                shell: {command: ls}
        """,
        "forbidden.run",
        "/do/0/shell/run",
        "/shell",
    ),
    ("use.extensions", "use:\n  extensions: []\n" + OK_TASK, "forbidden.use_extensions", "/use/extensions", None),
    ("use.authentications", "use:\n  authentications: {}\n" + OK_TASK, "forbidden.use_authentications", "/use/authentications", None),
    ("use.secrets", "use:\n  secrets: [token]\n" + OK_TASK, "forbidden.use_secrets", "/use/secrets", None),
    ("use.functions", "use:\n  functions: {}\n" + OK_TASK, "forbidden.use_functions", "/use/functions", None),
    ("use.catalogs", "use:\n  catalogs: {}\n" + OK_TASK, "forbidden.use_catalogs", "/use/catalogs", None),
    (
        "$secrets",
        """\
        do:
          - leak:
              set:
                token: ${ $secrets.token }
        """,
        "forbidden.secrets",
        "/do/0/leak/set/token",
        "/leak",
    ),
    ("schedule.every", "schedule:\n  every: PT1H\n" + OK_TASK, "forbidden.schedule_every", "/schedule/every", None),
    ("schedule.cron", "schedule:\n  cron: '0 * * * *'\n" + OK_TASK, "forbidden.schedule_cron", "/schedule/cron", None),
    ("schedule.on", "schedule:\n  on:\n    one: {with: {type: test.ping}}\n" + OK_TASK, "forbidden.schedule_on", "/schedule/on", None),
    *[
        (
            f"call {protocol}",
            f"do:\n  - remote:\n      call: {protocol}\n      with: {{}}\n",
            f"forbidden.call_{protocol}",
            "/do/0/remote/call",
            "/remote",
        )
        for protocol in ("http", "grpc", "openapi", "asyncapi")
    ],
    (
        "external schema",
        "input:\n  schema:\n    resource: {endpoint: 'https://example.org/schema.json'}\n" + OK_TASK,
        "schema.external",
        "/input/schema/resource",
        None,
    ),
]


@pytest.mark.parametrize("label, body, code, pointer, node", FORBIDDEN, ids=[row[0] for row in FORBIDDEN])
def test_forbidden_constructs_are_rejected_with_their_own_diagnostic(label, body, code, pointer, node):
    import textwrap

    found = [d for d in diagnostics(textwrap.dedent(body)) if d.code == code]
    assert found, f"{label}: {[str(d) for d in diagnostics(textwrap.dedent(body))]}"
    diagnostic = found[0]
    assert diagnostic.pointer == pointer
    assert diagnostic.node == node
    assert diagnostic.line is not None and diagnostic.column is not None
    assert diagnostic.message


def test_every_forbidden_construct_has_a_distinct_code():
    assert len({row[2] for row in FORBIDDEN}) == len(FORBIDDEN)


def test_diagnostic_points_to_the_source_line_and_column():
    text = scenario_text(
        """\
        do:
          - first:
              set: {a: 1}
          - second:
              call: nope:1@test
        """
    )
    [diagnostic] = KIT.load(text).diagnostics
    assert diagnostic.code == "call.unknown_capability"
    line = text.splitlines()[diagnostic.line - 1]
    assert line[diagnostic.column - 1 :].startswith("call: nope")


PROFILE_RULES = [
    ("duplicate task name", "do:\n  - a: {set: {x: 1}}\n  - a: {set: {x: 2}}\n", "task.duplicate_name"),
    ("reserved task name", "do:\n  - end: {set: {x: 1}}\n", "task.name_reserved"),
    ("task name format", "do:\n  - 'a/b': {set: {x: 1}}\n", "task.name_format"),
    ("unknown task kind", "do:\n  - a: {frobnicate: 1}\n", "task.unknown_kind"),
    ("ambiguous task kind", "do:\n  - a: {set: {x: 1}, wait: PT1S}\n", "task.ambiguous_kind"),
    ("unknown key", "do:\n  - a: {set: {x: 1}, retry: 3}\n", "unknown.key"),
    ("then to other scope", "do:\n  - g:\n      do:\n        - inner: {set: {}}\n  - a: {set: {}, then: inner}\n", "then.unknown_target"),
    ("bad capability ref", "do:\n  - a: {call: echo}\n", "call.ref_format"),
    ("unknown capability", "do:\n  - a: {call: 'missing:1@test'}\n", "call.unknown_capability"),
    ("static input violates schema", "do:\n  - a: {call: 'strict:1@test', with: {x: text}}\n", "call.input_schema"),
    ("expression syntax", "do:\n  - a: {set: {x: '${ .a | }'}}\n", "expression.syntax"),
    ("unknown variable", "do:\n  - a: {set: {x: '${ $item }'}}\n", "expression.unknown_variable"),
    ("wait too long", "do:\n  - a: {wait: P181D}\n", "limit.duration"),
    ("timeout too long", "do:\n  - a: {wait: PT1S, timeout: {after: P200D}}\n", "limit.duration"),
    ("bad duration", "do:\n  - a: {wait: P1M}\n", "duration.invalid"),
    ("try without catch", "do:\n  - a:\n      try:\n        - b: {set: {}}\n", "try.missing_catch"),
    ("unbounded retry", "do:\n  - a:\n      try:\n        - b: {set: {}}\n      catch:\n        retry: {delay: PT1S}\n", "retry.unbounded"),
    ("listen without type", "do:\n  - a:\n      listen:\n        to:\n          one: {with: {source: x}}\n", "listen.filter_type"),
    ("listen unknown event", "do:\n  - a:\n      listen:\n        to:\n          one: {with: {type: nope.event}}\n", "event.unknown_type"),
    ("listen unknown attribute", "do:\n  - a:\n      listen:\n        to:\n          one: {with: {type: vcs.pr.merged, data: {prid: '1'}}}\n", "event.unknown_attribute"),
    ("listen empty any", "do:\n  - a:\n      listen:\n        to:\n          any: []\n", "listen.empty_filters"),
    ("until without any", "do:\n  - a:\n      listen:\n        to:\n          all: [{with: {type: test.ping}}]\n          until: '${ true }'\n", "listen.until_requires_any"),
    (
        "correlation from is not a path",
        "do:\n  - a:\n      listen:\n        to:\n          one:\n            with: {type: vcs.pr.merged}\n            correlate:\n              pr: {from: '${ .data.pr_id + 1 }', expect: '1'}\n",
        "listen.correlation_from",
    ),
    (
        "correlation without expect",
        "do:\n  - a:\n      listen:\n        to:\n          one:\n            with: {type: vcs.pr.merged}\n            correlate:\n              pr: {from: '${ .data.pr_id }'}\n",
        "listen.correlation_expect",
    ),
    (
        "correlation path not in schema",
        "do:\n  - a:\n      listen:\n        to:\n          one:\n            with: {type: vcs.pr.merged}\n            correlate:\n              pr: {from: '${ .data.number }', expect: '1'}\n",
        "event.unknown_attribute",
    ),
    ("emit unknown event", "do:\n  - a:\n      emit:\n        event:\n          with: {type: nope.event}\n", "event.unknown_type"),
    ("emit data violates schema", "do:\n  - a:\n      emit:\n        event:\n          with: {type: test.notice, data: {text: 5}}\n", "emit.data_schema"),
    ("unsupported dsl", "do:\n  - a: {set: {}}\n", None),
    ("bad json schema", "input:\n  schema:\n    document: {type: 12}\n" + OK_TASK, "schema.invalid"),
    ("evaluate mode", "evaluate: {mode: loose}\n" + OK_TASK, "evaluate.mode"),
    ("unknown use.errors ref", "do:\n  - a: {raise: {error: missing}}\n", "reference.unknown_error"),
    ("empty fork", "do:\n  - a: {fork: {branches: []}}\n", "fork.empty"),
    ("loop variable shadows a built-in", "do:\n  - a:\n      for: {each: context, in: '${ [] }'}\n      do:\n        - b: {set: {}}\n", "for.variable"),
    ("catch.as shadows a built-in", "do:\n  - a:\n      try:\n        - b: {set: {}}\n      catch: {as: input}\n", "catch.as"),
    ("then on a fork branch", "do:\n  - p:\n      fork:\n        branches:\n          - a: {set: {}, then: end}\n", "then.fork_branch"),
    ("two switch defaults", "do:\n  - a:\n      switch:\n        - x: {then: end}\n        - y: {then: end}\n", "switch.multiple_defaults"),
]


@pytest.mark.parametrize("label, body, code", PROFILE_RULES, ids=[row[0] for row in PROFILE_RULES])
def test_profile_rules(label, body, code):
    found = codes(body)
    if code is None:
        assert found == []
    else:
        assert code in found, found


def test_node_and_nesting_limits():
    many = "do:\n" + "".join(f"  - t{i}: {{set: {{}}}}\n" for i in range(301))
    assert "limit.node_count" in codes(many)
    body, indent = "", ""
    for level in range(11):
        body += f"{indent}do:\n{indent}  - l{level}:\n"
        indent += "      "
    body += f"{indent}set: {{}}\n"
    assert "limit.nesting_depth" in codes(body)


def test_document_header_rules():
    text = "document:\n  dsl: '2.0.0'\n  namespace: Bad_NS\n  name: x\ndo:\n  - a: {set: {}}\n"
    found = [d.code for d in KIT.load(text).diagnostics]
    assert found == ["document.missing_field", "document.dsl_version", "document.name_format"]


def test_valid_scenario_pins_descriptors():
    result = KIT.load(
        scenario_text(
            """\
            do:
              - callIt: {call: 'echo:1@test', with: {a: 1}}
              - waitIt:
                  listen:
                    to:
                      one: {with: {type: vcs.pr.merged}}
            """
        )
    )
    assert result.ok, result.diagnostics
    assert list(result.snapshot.capabilities) == ["echo:1@test"]
    assert list(result.snapshot.events) == ["vcs.pr.merged"]
    assert result.snapshot.ref == "test/scenario:1.0.0"


def test_json_scenarios_validate_offline_with_positions():
    text = '{\n  "document": {"dsl": "1.0.3", "namespace": "test", "name": "json", "version": "1.0.0"},\n  "do": [\n    {"a": {"call": "http"}}\n  ]\n}'
    [diagnostic] = KIT.load(text).diagnostics
    assert (diagnostic.code, diagnostic.line, diagnostic.pointer) == ("forbidden.call_http", 4, "/do/0/a/call")
    assert KIT.load(text.replace('"http"', '"echo:1@test"')).ok
