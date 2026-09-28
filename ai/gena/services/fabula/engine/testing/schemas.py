import json
from typing import Any

import jsonschema


class Draft7SchemaValidator:
    """Reference `SchemaValidator` on `jsonschema` (Draft 7). Remote `$ref`s are not fetched."""

    def __init__(self) -> None:
        self._validators: dict[str, jsonschema.Draft7Validator] = {}

    def check_schema(self, schema: dict[str, Any]) -> list[str]:
        try:
            jsonschema.Draft7Validator.check_schema(schema)
        except jsonschema.SchemaError as exc:
            return [exc.message]
        return []

    def validate(self, schema: dict[str, Any], instance: Any) -> list[str]:
        key = json.dumps(schema, sort_keys=True)
        validator = self._validators.get(key)
        if validator is None:
            validator = self._validators[key] = jsonschema.Draft7Validator(schema)
        errors = sorted(validator.iter_errors(instance), key=lambda e: (list(e.absolute_path), e.message))
        return [f"{'/'.join(str(p) for p in error.absolute_path) or '<root>'}: {error.message}" for error in errors]
