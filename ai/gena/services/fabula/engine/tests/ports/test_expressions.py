import pytest

from ai.gena.services.fabula.engine.ports.expressions import ExpressionDiagnostics, ExpressionError
from ai.gena.services.fabula.engine.testing.expressions import TestOnlyExpressionEngine

ENGINE = TestOnlyExpressionEngine()
ARGS = {"context": {"count": 2, "ticket": {"id": "T-1"}}, "item": {"n": 3}}


def _eval(text, value=None, args=ARGS):
    compiled = ENGINE.compile(text)
    assert not isinstance(compiled, ExpressionDiagnostics), compiled
    return ENGINE.evaluate(compiled, value, args)


@pytest.mark.parametrize(
    "text, value, expected",
    [
        (".", {"a": 1}, {"a": 1}),
        (".a.b[0]", {"a": {"b": [7]}}, 7),
        (".a.b[-1]", {"a": {"b": [7, 8]}}, 8),
        ('.["odd key"]', {"odd key": 1}, 1),
        (".missing.deep", {}, None),
        ("$context.ticket.id", None, "T-1"),
        ("$item.n * 2", None, 6),
        ("$context.count + 1", None, 3),
        ("10 / 4", None, 2.5),
        ("7 % 3", None, 1),
        ('"a" + "b"', None, "ab"),
        ("[1, 2] + [3]", None, [1, 2, 3]),
        (".a == 1 and .b != 2", {"a": 1, "b": 3}, True),
        (".a < .b or false", {"a": 1, "b": 0}, False),
        ("null < false", None, True),
        ('1 < "a"', None, True),
        (".a | not", {"a": None}, True),
        (".a // 5", {"a": None}, 5),
        ("{id: .x, \"k\": [1, .y], $context, (.name): true}", {"x": 1, "y": 2, "name": "dyn"}, {"id": 1, "k": [1, 2], "context": ARGS["context"], "dyn": True}),
        ("{x}", {"x": 4}, {"x": 4}),
        (".items | length", {"items": [1, 2, 3]}, 3),
        ("keys", {"b": 1, "a": 2}, ["a", "b"]),
        (".s | ascii_upcase", {"s": "ab"}, "AB"),
        ("-(.a)", {"a": 2}, -2),
        (".a.b?", {"a": 5}, None),
    ],
)
def test_subset_evaluates(text, value, expected):
    assert _eval(text, value) == expected


@pytest.mark.parametrize(
    "text, value, message",
    [
        (".a.b", {"a": 5}, "cannot index number"),
        ("$nope", None, "$nope is not defined"),
        ("1 / 0", None, "division by zero"),
        ('"a" - 1', None, "cannot be combined"),
    ],
)
def test_subset_evaluation_errors(text, value, message):
    with pytest.raises(ExpressionError, match=message.replace("$", r"\$")):
        _eval(text, value)


@pytest.mark.parametrize("text", [".a |", ".[]", "if . then 1 else 2 end", "{a: }", "1 +", "foo(1)"])
def test_subset_rejects_unsupported_syntax(text):
    assert isinstance(ENGINE.compile(text), ExpressionDiagnostics)


def test_variables_are_reported_with_static_paths():
    compiled = ENGINE.compile("$context.ticket.id + $workflow.input.x + $item")
    assert compiled.variables == ("$context.ticket.id", "$workflow.input.x", "$item")


def test_limits_are_deterministic():
    tight = TestOnlyExpressionEngine(max_length=12, max_input_bytes=50, max_steps=5)
    assert isinstance(tight.compile("." * 13), ExpressionDiagnostics)
    with pytest.raises(ExpressionError, match="larger than"):
        tight.evaluate(tight.compile("."), list(range(100)), {})
    with pytest.raises(ExpressionError, match="step limit"):
        tight.evaluate(tight.compile("[1,2,3,4,5]"), None, {})
