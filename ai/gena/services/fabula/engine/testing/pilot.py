"""Pilot scenario: ticket -> agent -> merged PR -> soak -> health check -> approval -> comment."""

from datetime import timedelta

from ai.gena.services.fabula.engine.model.catalog import CapabilityDescriptor, EventTypeDescriptor, RetryDefaults
from ai.gena.services.fabula.engine.model.context import ExecutionContext
from ai.gena.services.fabula.engine.model.stimuli import OutcomeOk
from ai.gena.services.fabula.engine.testing.effects import PENDING
from ai.gena.services.fabula.engine.testing.kit import Kit
from ai.gena.services.fabula.engine.testing.runner import event
from ai.gena.services.fabula.engine.testing.world import World

PILOT = """\
document:
  dsl: '1.0.3'
  namespace: gena
  name: ticket-to-prod
  version: '1.0.0'
  title: From a ticket to a deployed change
input:
  schema:
    document:
      type: object
      required: [ticket]
      properties:
        ticket: {type: string}
timeout: {after: P60D}
do:
  - runAgent:
      call: 'agent.run:1@gena'
      with: {ticket: '${ .ticket }'}
      timeout: {after: P2D}
      export: {as: '${ {ticket: $input.ticket, pr: .pr_id} }'}
  - awaitMerge:
      listen:
        to:
          one:
            with: {type: vcs.pr.merged}
            correlate:
              pr: {from: '${ .data.pr_id }', expect: '${ $context.pr }'}
      timeout: {after: P30D}
  - soak:
      wait: P3D
  - healthCheck:
      try:
        - probe:
            call: 'http.healthcheck:1@platform'
            with: {url: 'https://service.example/health'}
      catch:
        errors: {with: {type: 'https://serverlessworkflow.io/spec/1.0.0/errors/communication'}}
        retry:
          delay: PT1M
          backoff: {exponential: {}}
          limit: {attempt: {count: 3}}
  - approve:
      call: 'human.approve:1@gena'
      with:
        ticket: '${ $context.ticket }'
        question: '${ "Ship " + $context.pr + "?" }'
      export: {as: '${ $context + {approved: .approved} }'}
  - decide:
      switch:
        - approved: {when: '${ $context.approved }', then: comment}
        - rejected: {then: end}
  - comment:
      call: 'tracker.comment:1@tracker'
      with:
        ticket: '${ $context.ticket }'
        text: '${ "Deployed " + $context.pr + " after a health check" }'
output:
  as: '${ {ticket: $context.ticket, pr: $context.pr, approved: $context.approved} }'
"""

PILOT_CAPABILITIES = [
    CapabilityDescriptor(
        ref="agent.run:1@gena",
        title="Run an agent on a ticket",
        input_schema={"type": "object", "required": ["ticket"], "properties": {"ticket": {"type": "string"}}},
        output_schema={"type": "object", "required": ["pr_id"], "properties": {"pr_id": {"type": "string"}}},
        effects=("writes:vcs",),
        default_timeout=timedelta(days=1),
    ),
    CapabilityDescriptor(
        ref="http.healthcheck:1@platform",
        input_schema={"type": "object", "required": ["url"]},
        output_schema={"type": "object", "required": ["status"]},
        effects=("reads:network",),
        default_timeout=timedelta(minutes=5),
        default_retry=RetryDefaults(max_retries=1, delay=timedelta(seconds=5)),
    ),
    CapabilityDescriptor(
        ref="human.approve:1@gena",
        input_schema={"type": "object", "required": ["ticket", "question"]},
        output_schema={"type": "object", "required": ["approved"], "properties": {"approved": {"type": "boolean"}}},
        effects=("asks:human",),
        cancellable=True,
    ),
    CapabilityDescriptor(
        ref="tracker.comment:1@tracker",
        input_schema={"type": "object", "required": ["ticket", "text"]},
        effects=("writes:tracker",),
    ),
]

PILOT_EVENTS = [
    EventTypeDescriptor(
        type="vcs.pr.merged",
        data_schema={"type": "object", "properties": {"pr_id": {"type": "string"}, "repo": {"type": "string"}}},
    )
]

PILOT_REF = "gena/ticket-to-prod:1.0.0"


def pilot_kit() -> Kit:
    kit = Kit()
    for descriptor in PILOT_CAPABILITIES:
        kit.capabilities.add(descriptor)
    for descriptor in PILOT_EVENTS:
        kit.events.add(descriptor)
    return kit


def pilot_world(crash_at: int | set[int] | None = None, **options) -> World:
    world = World(pilot_kit(), crash_at=crash_at, **options)
    world.scenarios.publish(world.kit.snapshot(PILOT))
    world.invoker.behaviours.update(
        {
            "agent.run:1@gena": lambda request: OutcomeOk(output={"pr_id": "PR-" + request.input["ticket"]}),
            "http.healthcheck:1@platform": lambda request: OutcomeOk(output={"status": "ok"}),
            "human.approve:1@gena": lambda request: PENDING,
            "tracker.comment:1@tracker": lambda request: OutcomeOk(output={"comment_id": "C-1"}),
        }
    )
    return world


async def drive_pilot(world: World, ticket: str = "T-42", fabula_id: str = "pilot-1") -> None:
    """External actions around the pilot; every step survives host crashes."""
    context = ExecutionContext(run_as="gena-bot", trigger={"ticket": ticket}, labels={"team": "gena"})
    await world.start(fabula_id, PILOT_REF, {"ticket": ticket}, context)
    world.bus.publish_external(event("vcs.pr.merged", {"pr_id": "PR-OTHER", "repo": "gena"}, id="merge-other"))
    world.bus.publish_external(event("vcs.pr.merged", {"pr_id": f"PR-{ticket}", "repo": "gena"}, id="merge-1"))
    await world.pump()
    await world.advance(timedelta(days=3))
    [approval] = world.invoker.pending("human.approve:1@gena")
    world.invoker.complete(approval, OutcomeOk(output={"approved": True}))
    await world.pump()
