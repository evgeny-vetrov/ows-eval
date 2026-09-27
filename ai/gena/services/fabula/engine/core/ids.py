"""Deterministic identifiers of commands.

`{fabula_id}/{path}@{visit}.{attempt}/{kind}`: the same logical operation gets the same
id on every replay, and `(visit, attempt)` never repeats for a node.
"""

WORKFLOW = "@workflow"


def op_id(fabula_id: str, path: str, visit: int, attempt: int, kind: str) -> str:
    return f"{fabula_id}/{path.lstrip('/')}@{visit}.{attempt}/{kind}"


def workflow_op_id(fabula_id: str, kind: str) -> str:
    return f"{fabula_id}/{WORKFLOW}/{kind}"


def next_generation(identifier: str) -> str:
    """`x` -> `x~2` -> `x~3`: a rescheduled timer or renewed subscription gets a new id."""
    base, _, generation = identifier.rpartition("~")
    if base and generation.isdigit():
        return f"{base}~{int(generation) + 1}"
    return f"{identifier}~2"
