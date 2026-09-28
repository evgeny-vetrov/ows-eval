"""JSON Schema port (pure), Draft 7.

Contract: both methods are deterministic, never fetch remote `$ref`s and return
human-readable messages instead of raising. An empty list means "valid".
"""

from typing import Any, Protocol


class SchemaValidator(Protocol):
    def check_schema(self, schema: dict[str, Any]) -> list[str]: ...

    def validate(self, schema: dict[str, Any], instance: Any) -> list[str]: ...
