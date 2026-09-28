"""Versions of a test scenario and helpers that put fabulas at chosen points."""

from datetime import timedelta

from ai.gena.services.fabula.engine.model.problem import ErrorType, Problem
from ai.gena.services.fabula.engine.model.stimuli import OutcomeError, OutcomeOk
from ai.gena.services.fabula.engine.testing.effects import PENDING
from ai.gena.services.fabula.engine.testing.kit import scenario_text
from ai.gena.services.fabula.engine.testing.runner import event
from ai.gena.services.fabula.manager.model.common import Actor
from ai.gena.services.fabula.manager.model.fabulas import Selector, StartFabula, StartResult
from ai.gena.services.fabula.manager.model.scenarios import PublishVersion, ScenarioVersion
from ai.gena.services.fabula.manager.testing.stand import ALICE, Stand

SCENARIO = "test/order"

V1 = """
do:
  - prepare: {set: {}, export: {as: '${ {ticket: $input.ticket} }'}}
  - ask: {call: 'echo:1@test', with: {ticket: '${ $context.ticket }'}}
  - merged:
      listen:
        to:
          one: {with: {type: vcs.pr.merged}}
  - finish: {set: {version: 1}}
"""

# Only the tail changes: every running fabula moves in flight.
V2 = V1.replace("finish: {set: {version: 1}}", "finish: {set: {version: 2, ticket: '${ $context.ticket }'}}")

# The wait is renamed; the diff suggests the mapping.
V2_RENAMED = V2.replace("  - merged:", "  - prMerged:")

# The call moves to another capability, the listen filters change and the tail needs
# a context key the fabulas do not have.
V3 = """
do:
  - prepare: {set: {}, export: {as: '${ {ticket: $input.ticket} }'}}
  - ask: {call: 'strict:1@test', with: {x: 1}}
  - merged:
      listen:
        to:
          one: {with: {type: vcs.pr.merged, data: {repo: gena}}}
  - finish: {set: {version: 3, owner: '${ $context.owner }'}}
"""

# `ask` is gone: a fabula waiting there has no counterpart.
V4 = """
do:
  - prepare: {set: {}, export: {as: '${ {ticket: $input.ticket} }'}}
  - merged:
      listen:
        to:
          one: {with: {type: vcs.pr.merged}}
  - finish: {set: {version: 4}}
"""

MERGED = event("vcs.pr.merged", {"pr_id": "PR-1", "repo": "gena"}, id="merged-1")


def order(body: str, version: str) -> str:
    return scenario_text(body, name="order", version=version)


async def publish(stand: Stand, body: str, version: str, actor: Actor = ALICE, scenario: str = SCENARIO) -> ScenarioVersion:
    text = scenario_text(body, name=scenario.split("/")[1], version=version)
    return await stand.manager.registry.publish(actor, scenario, PublishVersion(request_id=f"publish-{scenario.replace('/', '.')}-{version}", source=text))


def hold_calls(stand: Stand, capability: str = "echo:1@test") -> None:
    """Invocations of `capability` stay pending until `finish_call`."""
    stand.world.invoker.behaviours[capability] = lambda request: PENDING


async def start(stand: Stand, request_id: str, selector: Selector = Selector(), actor: Actor = ALICE, **fields) -> StartResult:
    request = StartFabula(request_id=request_id, scenario=fields.pop("scenario", SCENARIO), selector=selector, input=fields.pop("input", {"ticket": "T-1"}), **fields)
    return await stand.manager.fabulas.start(actor, request)


def pending_invocations(stand: Stand, fabula_id: str) -> list[str]:
    return [i for i, e in stand.world.invoker.executions.items() if e.fabula_id == fabula_id and e.outcome is PENDING and not e.cancelled]


async def finish_call(stand: Stand, fabula_id: str, output=None, fail: bool = False) -> None:
    [invocation] = pending_invocations(stand, fabula_id)
    outcome = OutcomeError(problem=Problem.of(ErrorType.RUNTIME, "boom")) if fail else OutcomeOk(output=output or {})
    stand.world.invoker.complete(invocation, outcome)
    await stand.world.pump()


async def deliver_merged(stand: Stand) -> None:
    stand.world.bus.publish_external(MERGED)
    await stand.world.pump()


async def fleet(stand: Stand) -> dict[str, str]:
    """Four fabulas on V1: waiting on the call, waiting on the event, failed at the call
    and completed. Returns their ids by role."""
    hold_calls(stand)
    ids = {}
    for role in ("at_call", "at_event", "failed", "done"):
        ids[role] = (await start(stand, f"start-{role}", input={"ticket": f"T-{role}"})).fabula_id
    await finish_call(stand, ids["failed"], fail=True)
    await finish_call(stand, ids["done"])
    stand.world.bus.publish_external(event("vcs.pr.merged", {"pr_id": "PR-0"}, id="merged-0"))
    await stand.world.pump()
    # Later than the event above, so the lookback of the next listen does not see it.
    stand.clock.advance(timedelta(minutes=1))
    await finish_call(stand, ids["at_event"])
    return ids
