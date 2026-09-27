"""Helpers over plain JSON values.

JSON values that flow through the engine are treated as immutable: code builds new
containers instead of mutating shared ones, so state, commands and observations may
safely share the same objects.
"""

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from pydantic import JsonValue

Json = JsonValue

_MISSING = object()


def dumps_canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(dumps_canonical(value).encode("utf-8")).hexdigest()


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def json_equal(left: Any, right: Any) -> bool:
    """Equality with JSON semantics: `true` is not `1`, `1` equals `1.0`."""
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if is_number(left) and is_number(right):
        return left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(json_equal(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(json_equal(a, b) for a, b in zip(left, right))
    return type(left) is type(right) and left == right


def exceeds_size(value: Any, limit: int) -> bool:
    """True when the compact JSON form of `value` is longer than `limit` bytes.

    Walks the value with an early exit, so a small limit on a huge value stays cheap.
    Strings are counted by characters, which is a lower bound of the UTF-8 size.
    """
    budget = limit
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            budget -= 2 + len(item)
            for key, nested in item.items():
                budget -= len(key) + 3
                stack.append(nested)
        elif isinstance(item, list):
            budget -= 2 + len(item)
            stack.extend(item)
        elif isinstance(item, str):
            budget -= len(item) + 2
        else:
            budget -= 5
        if budget < 0:
            return True
    return False


def get_path(value: Any, segments: Sequence[str]) -> Any:
    """Follow dot-path segments; returns `MISSING` when a segment does not resolve."""
    current = value
    for segment in segments:
        if isinstance(current, dict):
            if segment not in current:
                return _MISSING
            current = current[segment]
        elif isinstance(current, list) and segment.lstrip("-").isdigit():
            index = int(segment)
            if not -len(current) <= index < len(current):
                return _MISSING
            current = current[index]
        else:
            return _MISSING
    return current


MISSING = _MISSING


def merge_patch(target: Any, patch: Any) -> Any:
    """RFC 7386 JSON merge patch; returns a new value."""
    if not isinstance(patch, dict):
        return patch
    result = dict(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = merge_patch(result.get(key), value)
    return result
