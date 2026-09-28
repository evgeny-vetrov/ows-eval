"""Step journal rebuilt from observations."""

from collections.abc import Iterable
from datetime import datetime
from typing import Literal

from ai.gena.services.fabula.engine.model.base import Frozen, UtcDatetime
from ai.gena.services.fabula.engine.model.jsonvalue import Json
from ai.gena.services.fabula.engine.model.observations import Observation, WaitInfo
from ai.gena.services.fabula.engine.model.problem import Problem

StepStatus = Literal["running", "completed", "failed", "cancelled", "skipped", "retrying"]


class StepRecord(Frozen):
    node: str
    visit: int
    attempt: int
    task: str | None = None
    status: StepStatus
    started_at: UtcDatetime
    finished_at: UtcDatetime | None = None
    input: Json = None
    output: Json = None
    problem: Problem | None = None
    skipped_by: Literal["condition", "control"] | None = None


def build_journal(observations: Iterable[Observation]) -> list[StepRecord]:
    """One record per (node, visit, attempt), in the order the steps started."""
    records: dict[tuple[str, int, int], StepRecord] = {}
    latest: dict[tuple[str, int], tuple[str, int, int]] = {}

    def update(key, **changes) -> None:
        records[key] = records[key].model_copy(update=changes)

    for observation in observations:
        kind = observation.kind
        if kind == "node_started":
            key = (observation.node, observation.visit, observation.attempt)
            records[key] = StepRecord(
                node=observation.node,
                visit=observation.visit,
                attempt=observation.attempt,
                task=observation.task,
                status="running",
                started_at=observation.at,
                input=observation.input,
            )
            latest[(observation.node, observation.visit)] = key
        elif kind == "node_skipped":
            key = latest.get((observation.node, observation.visit))
            if key is None or observation.reason == "condition":
                key = (observation.node, observation.visit, 1)
                records[key] = StepRecord(
                    node=observation.node,
                    visit=observation.visit,
                    attempt=1,
                    status="skipped",
                    started_at=observation.at,
                    finished_at=observation.at,
                    output=observation.output,
                    skipped_by="condition",
                )
            else:
                update(key, skipped_by="control")
        elif kind in ("node_completed", "node_failed", "node_cancelled"):
            key = (observation.node, observation.visit, observation.attempt)
            if key not in records:
                continue
            if kind == "node_completed":
                update(key, status="completed", finished_at=observation.at, output=observation.output)
            elif kind == "node_failed":
                update(key, status="failed", finished_at=observation.at, problem=observation.problem)
            else:
                update(key, status="cancelled", finished_at=observation.at)
        elif kind == "retry_scheduled":
            key = (observation.node, observation.visit, observation.next_attempt - 1)
            if key in records:
                update(key, status="retrying", finished_at=observation.at, problem=observation.problem)
    return list(records.values())


def current_waits(observations: Iterable[Observation]) -> tuple[WaitInfo, ...]:
    """What the fabula waits for after the last observed transition."""
    waits: tuple[WaitInfo, ...] = ()
    for observation in observations:
        if observation.kind == "waiting":
            waits = observation.waits
        elif observation.kind in ("fabula_completed", "fabula_failed", "fabula_cancelled"):
            waits = ()
    return waits


def last_activity(observations: Iterable[Observation]) -> datetime | None:
    moment = None
    for observation in observations:
        moment = observation.at
    return moment
