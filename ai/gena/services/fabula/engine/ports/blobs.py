"""Pure functions that move large values of a persisted document out to a blob store.

A replaced value becomes `{"$blob": "sha256:<hex>", "size": n}`. User dictionaries that
contain `$blob` or `$literal` keys are wrapped into `{"$literal": {...}}`, so
`internalize(externalize(d)) == d` for every JSON document. The root is never replaced.
"""

import hashlib
import json
from collections.abc import Callable
from typing import Any

BLOB = "$blob"
LITERAL = "$literal"
_RESERVED = frozenset((BLOB, LITERAL))


def _compact(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def externalize(document: dict[str, Any], threshold: int) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Replace every non-root value whose compact JSON exceeds `threshold` bytes.

    Children are processed first, so only what is still large after replacing its large
    descendants is moved out. Returns the new document and the blobs to store.
    """
    blobs: dict[str, bytes] = {}

    def encode(value: Any, root: bool) -> tuple[Any, int]:
        if isinstance(value, dict):
            items = {}
            size = 2 + max(len(value) - 1, 0)
            for key, nested in value.items():
                items[key], nested_size = encode(nested, False)
                size += len(_compact(key).encode()) + 1 + nested_size
            if _RESERVED & items.keys():
                items = {LITERAL: items}
                size += len(_compact(LITERAL).encode()) + 3
            new: Any = items
        elif isinstance(value, list):
            new = []
            size = 2 + max(len(value) - 1, 0)
            for nested in value:
                encoded, nested_size = encode(nested, False)
                new.append(encoded)
                size += nested_size
        else:
            new = value
            size = len(_compact(value).encode())
        if root or size <= threshold or not isinstance(value, (dict, list, str)):
            return new, size
        data = _compact(new).encode()
        ref = "sha256:" + hashlib.sha256(data).hexdigest()
        blobs[ref] = data
        stub = {BLOB: ref, "size": len(data)}
        return stub, len(_compact(stub).encode())

    encoded, _ = encode(document, True)
    return encoded, blobs


def internalize(document: dict[str, Any], fetch: Callable[[str], bytes]) -> dict[str, Any]:
    """Inverse of `externalize`; `fetch(ref)` returns the stored bytes."""

    def decode(value: Any) -> Any:
        if isinstance(value, dict):
            if BLOB in value and set(value) <= {BLOB, "size"}:
                return decode(json.loads(fetch(value[BLOB])))
            if set(value) == {LITERAL}:
                return {key: decode(nested) for key, nested in value[LITERAL].items()}
            return {key: decode(nested) for key, nested in value.items()}
        if isinstance(value, list):
            return [decode(nested) for nested in value]
        return value

    return decode(document)
