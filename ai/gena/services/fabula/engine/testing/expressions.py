"""TEST-ONLY expression evaluator for a tiny jq-like subset. It is NOT jq.

The build has no Python jq bindings, so the core is exercised against this evaluator.
Production deployments plug a real jq adapter into the `ExpressionEngine` port.

Supported: literals, `.`, paths (`.a.b[0]`, `.["k"]`, `.[expr]`, optional `?`),
variables (`$x`, `$x.a`), `|`, `==` `!=` `<` `<=` `>` `>=`, `and` `or`, `//`,
`+ - * / %`, unary minus, parentheses, array `[a, b]` and object `{a: x, "b": y,
(k): v, $v, name}` construction, and the filters `not`, `length`, `keys`, `type`,
`tostring`, `tonumber`, `ascii_downcase`, `ascii_upcase`. Values are single JSON
values: there are no streams, `.[]`, `,` outside constructors, or `if`.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ai.gena.services.fabula.engine.model.jsonvalue import exceeds_size, is_number, json_equal
from ai.gena.services.fabula.engine.ports.expressions import ExpressionDiagnostics, ExpressionError

_TOKEN = re.compile(
    r"""\s*(?:
        (?P<number>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
      | (?P<string>"(?:[^"\\]|\\.)*")
      | (?P<var>\$[A-Za-z_][A-Za-z0-9_]*)
      | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
      | (?P<field>\.[A-Za-z_][A-Za-z0-9_]*)
      | (?P<op>==|!=|<=|>=|//|[.\[\](){}|,:<>+\-*/%?])
    )""",
    re.VERBOSE,
)

_FILTERS = frozenset(("not", "length", "keys", "type", "tostring", "tonumber", "ascii_downcase", "ascii_upcase"))


class _Syntax(Exception):
    def __init__(self, message: str, offset: int):
        super().__init__(message)
        self.offset = offset


@dataclass(frozen=True)
class Compiled:
    text: str
    variables: tuple[str, ...]
    tree: Any = field(repr=False, compare=False)


def _tokenize(text: str) -> list[tuple[str, str, int]]:
    tokens = []
    position = 0
    while position < len(text):
        if text[position:].strip() == "":
            break
        match = _TOKEN.match(text, position)
        if not match or match.end() == position:
            raise _Syntax(f"unexpected character {text[position:].lstrip()[:1]!r}", position)
        kind = match.lastgroup
        tokens.append((kind, match.group(kind), match.start(kind)))
        position = match.end()
    tokens.append(("end", "", len(text)))
    return tokens


class _Parser:
    def __init__(self, text: str):
        self.tokens = _tokenize(text)
        self.index = 0
        self.variables: list[str] = []

    def peek(self, value: str | None = None, kind: str | None = None) -> bool:
        token_kind, token_value, _ = self.tokens[self.index]
        return (value is None or token_value == value) and (kind is None or token_kind == kind)

    def take(self) -> tuple[str, str, int]:
        token = self.tokens[self.index]
        self.index += 1
        return token

    def expect(self, value: str) -> None:
        kind, token, offset = self.take()
        if token != value or kind == "string":
            raise _Syntax(f"expected {value!r}, got {token or 'end of expression'!r}", offset)

    def parse(self) -> Any:
        tree = self.pipe()
        kind, token, offset = self.tokens[self.index]
        if kind != "end":
            raise _Syntax(f"unexpected {token!r}", offset)
        return tree

    def pipe(self) -> Any:
        tree = self.disjunction()
        while self.peek("|", "op"):
            self.take()
            tree = ("pipe", tree, self.disjunction())
        return tree

    def disjunction(self) -> Any:
        tree = self.conjunction()
        while self.peek("or", "ident"):
            self.take()
            tree = ("or", tree, self.conjunction())
        return tree

    def conjunction(self) -> Any:
        tree = self.comparison()
        while self.peek("and", "ident"):
            self.take()
            tree = ("and", tree, self.comparison())
        return tree

    def comparison(self) -> Any:
        tree = self.alternative()
        kind, token, _ = self.tokens[self.index]
        if kind == "op" and token in ("==", "!=", "<", "<=", ">", ">="):
            self.take()
            tree = ("cmp", token, tree, self.alternative())
        return tree

    def alternative(self) -> Any:
        tree = self.additive()
        while self.peek("//", "op"):
            self.take()
            tree = ("alt", tree, self.additive())
        return tree

    def additive(self) -> Any:
        tree = self.multiplicative()
        while self.tokens[self.index][0] == "op" and self.tokens[self.index][1] in ("+", "-"):
            _, token, _ = self.take()
            tree = ("arith", token, tree, self.multiplicative())
        return tree

    def multiplicative(self) -> Any:
        tree = self.unary()
        while self.tokens[self.index][0] == "op" and self.tokens[self.index][1] in ("*", "/", "%"):
            _, token, _ = self.take()
            tree = ("arith", token, tree, self.unary())
        return tree

    def unary(self) -> Any:
        if self.peek("-", "op"):
            self.take()
            return ("neg", self.unary())
        return self.postfix(self.primary())

    def postfix(self, tree: Any) -> Any:
        segments: list[str] | None = [] if tree[0] == "var" else None
        while True:
            kind, token, offset = self.tokens[self.index]
            if kind == "field":
                self.take()
                tree = ("index", tree, ("lit", token[1:]), False)
                if segments is not None:
                    segments.append(token[1:])
            elif kind == "op" and token == "." and self.tokens[self.index + 1][0] == "string":
                self.take()
                _, literal, _ = self.take()
                key = json.loads(literal)
                tree = ("index", tree, ("lit", key), False)
                if segments is not None:
                    segments.append(key)
            elif kind == "op" and token == "[":
                self.take()
                if self.peek("]", "op"):
                    raise _Syntax("iteration '.[]' is not supported by the test evaluator", offset)
                key = self.pipe()
                self.expect("]")
                tree = ("index", tree, key, False)
                segments = None
            elif kind == "op" and token == "?":
                self.take()
                tree = ("try", tree)
            else:
                break
        if tree[0] != "var" and segments is not None and segments:
            self.variables[-1] = self.variables[-1] + "".join("." + s for s in segments)
        return tree

    def primary(self) -> Any:
        kind, token, offset = self.take()
        if kind == "number":
            number = float(token)
            return ("lit", int(number) if number.is_integer() and "." not in token and "e" not in token.lower() else number)
        if kind == "string":
            return ("lit", json.loads(token))
        if kind == "var":
            self.variables.append(token)
            return ("var", token[1:])
        if kind == "field":
            return ("index", ("identity",), ("lit", token[1:]), False)
        if kind == "ident":
            if token in ("true", "false"):
                return ("lit", token == "true")
            if token == "null":
                return ("lit", None)
            if token in _FILTERS:
                return ("filter", token)
            raise _Syntax(f"unknown function '{token}'", offset)
        if kind == "op" and token == ".":
            if self.tokens[self.index][0] == "string":
                _, literal, _ = self.take()
                return ("index", ("identity",), ("lit", json.loads(literal)), False)
            return ("identity",)
        if kind == "op" and token == "(":
            tree = self.pipe()
            self.expect(")")
            return tree
        if kind == "op" and token == "[":
            items = []
            if not self.peek("]", "op"):
                items.append(self.pipe())
                while self.peek(",", "op"):
                    self.take()
                    items.append(self.pipe())
            self.expect("]")
            return ("array", tuple(items))
        if kind == "op" and token == "{":
            return self.object()
        raise _Syntax(f"unexpected {token or 'end of expression'!r}", offset)

    def object(self) -> Any:
        entries = []
        if not self.peek("}", "op"):
            entries.append(self.entry())
            while self.peek(",", "op"):
                self.take()
                entries.append(self.entry())
        self.expect("}")
        return ("object", tuple(entries))

    def entry(self) -> tuple[Any, Any]:
        kind, token, offset = self.take()
        if kind == "var":
            self.variables.append(token)
            return ("lit", token[1:]), ("var", token[1:])
        if kind in ("ident", "string"):
            key = token if kind == "ident" else json.loads(token)
            if not self.peek(":", "op"):
                return ("lit", key), ("index", ("identity",), ("lit", key), False)
            self.take()
            return ("lit", key), self.alternative_value()
        if kind == "op" and token == "(":
            key = self.pipe()
            self.expect(")")
            self.expect(":")
            return key, self.alternative_value()
        raise _Syntax(f"unexpected {token!r} in object construction", offset)

    def alternative_value(self) -> Any:
        tree = self.disjunction()
        while self.peek("|", "op"):
            self.take()
            tree = ("pipe", tree, self.disjunction())
        return tree


_TYPE_ORDER = {type(None): 0, bool: 1, int: 3, float: 3, str: 4, list: 5, dict: 6}


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if is_number(value):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _rank(value: Any) -> tuple:
    if isinstance(value, bool):
        return (1, int(value))
    return (_TYPE_ORDER[type(value)],)


def _compare(left: Any, right: Any) -> int:
    left_rank, right_rank = _rank(left), _rank(right)
    if left_rank[0] != right_rank[0] or isinstance(left, bool):
        return (left_rank > right_rank) - (left_rank < right_rank)
    if isinstance(left, (list, dict)):
        left_items = sorted(left.items()) if isinstance(left, dict) else left
        right_items = sorted(right.items()) if isinstance(right, dict) else right
        for a, b in zip(left_items, right_items):
            result = _compare(list(a), list(b)) if isinstance(left, dict) else _compare(a, b)
            if result:
                return result
        return (len(left_items) > len(right_items)) - (len(left_items) < len(right_items))
    return (left > right) - (left < right)


def _truthy(value: Any) -> bool:
    return value is not None and value is not False


def _normalize(number: Any) -> Any:
    if isinstance(number, float) and number.is_integer() and abs(number) < 2**53:
        return int(number)
    return number


class _Evaluator:
    def __init__(self, args: Mapping[str, Any], max_steps: int):
        self.args = args
        self.steps = max_steps

    def eval(self, node: Any, value: Any) -> Any:
        self.steps -= 1
        if self.steps < 0:
            raise ExpressionError("evaluation step limit exceeded")
        tag = node[0]
        if tag == "identity":
            return value
        if tag == "lit":
            return node[1]
        if tag == "var":
            if node[1] not in self.args:
                raise ExpressionError(f"${node[1]} is not defined")
            return self.args[node[1]]
        if tag == "index":
            return self.index(self.eval(node[1], value), self.eval(node[2], value))
        if tag == "try":
            try:
                return self.eval(node[1], value)
            except ExpressionError:
                return None
        if tag == "pipe":
            return self.eval(node[2], self.eval(node[1], value))
        if tag == "and":
            return _truthy(self.eval(node[1], value)) and _truthy(self.eval(node[2], value))
        if tag == "or":
            return _truthy(self.eval(node[1], value)) or _truthy(self.eval(node[2], value))
        if tag == "alt":
            left = self.eval(node[1], value)
            return left if _truthy(left) else self.eval(node[2], value)
        if tag == "cmp":
            return self.compare(node[1], self.eval(node[2], value), self.eval(node[3], value))
        if tag == "arith":
            return self.arith(node[1], self.eval(node[2], value), self.eval(node[3], value))
        if tag == "neg":
            operand = self.eval(node[1], value)
            if not is_number(operand):
                raise ExpressionError(f"cannot negate {_type_name(operand)}")
            return -operand
        if tag == "array":
            return [self.eval(item, value) for item in node[1]]
        if tag == "object":
            result = {}
            for key_node, value_node in node[1]:
                key = self.eval(key_node, value)
                if not isinstance(key, str):
                    raise ExpressionError(f"object keys must be strings, got {_type_name(key)}")
                result[key] = self.eval(value_node, value)
            return result
        if tag == "filter":
            return self.filter(node[1], value)
        raise ExpressionError(f"unsupported node {tag}")

    @staticmethod
    def index(container: Any, key: Any) -> Any:
        if container is None:
            return None
        if isinstance(key, str):
            if not isinstance(container, dict):
                raise ExpressionError(f"cannot index {_type_name(container)} with \"{key}\"")
            return container.get(key)
        if is_number(key) and not isinstance(key, bool):
            if not isinstance(container, list):
                raise ExpressionError(f"cannot index {_type_name(container)} with number")
            position = int(key)
            if -len(container) <= position < len(container):
                return container[position]
            return None
        raise ExpressionError(f"cannot index {_type_name(container)} with {_type_name(key)}")

    @staticmethod
    def compare(op: str, left: Any, right: Any) -> bool:
        if op == "==":
            return json_equal(left, right)
        if op == "!=":
            return not json_equal(left, right)
        result = _compare(left, right)
        return {"<": result < 0, "<=": result <= 0, ">": result > 0, ">=": result >= 0}[op]

    @staticmethod
    def arith(op: str, left: Any, right: Any) -> Any:
        if op == "+":
            if left is None:
                return right
            if right is None:
                return left
            if is_number(left) and is_number(right):
                return left + right
            if isinstance(left, str) and isinstance(right, str):
                return left + right
            if isinstance(left, list) and isinstance(right, list):
                return left + right
            if isinstance(left, dict) and isinstance(right, dict):
                return {**left, **right}
        elif op == "-":
            if is_number(left) and is_number(right):
                return left - right
            if isinstance(left, list) and isinstance(right, list):
                return [item for item in left if not any(json_equal(item, other) for other in right)]
        elif is_number(left) and is_number(right):
            if op == "*":
                return left * right
            if right == 0:
                raise ExpressionError("division by zero")
            if op == "/":
                return _normalize(left / right)
            return int(left) % int(right)
        raise ExpressionError(f"{_type_name(left)} and {_type_name(right)} cannot be combined with '{op}'")

    @staticmethod
    def filter(name: str, value: Any) -> Any:
        if name == "not":
            return not _truthy(value)
        if name == "length":
            if value is None:
                return 0
            if isinstance(value, bool):
                raise ExpressionError("boolean has no length")
            if is_number(value):
                return abs(value)
            return len(value)
        if name == "keys":
            if isinstance(value, dict):
                return sorted(value)
            if isinstance(value, list):
                return list(range(len(value)))
            raise ExpressionError(f"{_type_name(value)} has no keys")
        if name == "type":
            return _type_name(value)
        if name == "tostring":
            return value if isinstance(value, str) else json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        if name == "tonumber":
            if is_number(value):
                return value
            try:
                return _normalize(float(value))
            except (TypeError, ValueError):
                raise ExpressionError(f"cannot parse {value!r} as a number") from None
        if not isinstance(value, str):
            raise ExpressionError(f"{name} input must be a string")
        return value.lower() if name == "ascii_downcase" else value.upper()


class TestOnlyExpressionEngine:
    """`ExpressionEngine` for tests. See the module docstring: this is not jq.

    Limits: `max_length` characters per expression, `max_input_bytes` of `.` and
    `max_steps` evaluation steps (deterministic, so replays hit the same limit).
    """

    __test__ = False

    def __init__(self, max_length: int = 4096, max_input_bytes: int = 4 * 1024 * 1024, max_steps: int = 100_000):
        self.max_length = max_length
        self.max_input_bytes = max_input_bytes
        self.max_steps = max_steps

    def compile(self, text: str) -> Compiled | ExpressionDiagnostics:
        if len(text) > self.max_length:
            return ExpressionDiagnostics(text=text, messages=(f"expression is longer than {self.max_length} characters",))
        parser = None
        try:
            parser = _Parser(text)
            tree = parser.parse()
        except _Syntax as exc:
            return ExpressionDiagnostics(text=text, messages=(str(exc),), offset=exc.offset)
        return Compiled(text=text, variables=tuple(dict.fromkeys(parser.variables)), tree=tree)

    def evaluate(self, compiled: Compiled, input: Any, args: Mapping[str, Any]) -> Any:
        if exceeds_size(input, self.max_input_bytes):
            raise ExpressionError(f"expression input is larger than {self.max_input_bytes} bytes")
        return _Evaluator(args, self.max_steps).eval(compiled.tree, input)
