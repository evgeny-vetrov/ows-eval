"""The sandbox API: its operations in the manager's `Operation` format and their
handlers. Dispatched, checked and documented like every other operation."""

import uuid

from ai.gena.services.fabula.engine.model.event import Event
from ai.gena.services.fabula.manager.api.operations import Operation
from ai.gena.services.fabula.manager.model.common import Actor
from ai.gena.services.fabula.manager.model.errors import Unprocessable
from ai.gena.services.fabula.manager.ports.catalogs import EventDirectory
from ai.gena.services.fabula.server.auth import SANDBOX_PERMISSION
from ai.gena.services.fabula.server.sandbox import models
from ai.gena.services.fabula.server.sandbox.runtime import Sandbox

SANDBOX_TAG = {
    "name": "sandbox",
    "description": (
        "The in-process runtime of a development server: complete capability calls, publish events, "
        "move the clock. Present only when the server runs the sandbox."
    ),
}

SANDBOX_OPERATIONS: tuple[Operation, ...] = (
    Operation("get_sandbox", "GET", "/v1/sandbox", SANDBOX_PERMISSION, "sandbox", "Clock, queues, timers and dead letters", models.SandboxStatus),
    Operation(
        "list_sandbox_invocations", "GET", "/v1/sandbox/invocations", SANDBOX_PERMISSION, "sandbox",
        "Capability calls made by fabulas", models.InvocationList, query=models.InvocationQuery,
    ),
    Operation(
        "complete_sandbox_invocation", "POST", "/v1/sandbox/invocations:complete", SANDBOX_PERMISSION, "sandbox",
        "Give a pending call its result", models.Invocation, body=models.CompleteInvocation, errors=(404, 409, 500, 503),
    ),
    Operation(
        "list_sandbox_events", "GET", "/v1/sandbox/events", SANDBOX_PERMISSION, "sandbox",
        "Events published by fabulas and through the sandbox", models.SandboxEventList, query=models.EventQuery,
    ),
    Operation(
        "publish_sandbox_event", "POST", "/v1/sandbox/events", SANDBOX_PERMISSION, "sandbox",
        "Publish an event from the outside world", models.SandboxEvent, body=models.PublishEvent, status=201, errors=(422, 500, 503),
    ),
    Operation(
        "advance_sandbox_clock", "POST", "/v1/sandbox/clock:advance", SANDBOX_PERMISSION, "sandbox",
        "Move the clock forward; due timers fire", models.SandboxStatus, body=models.AdvanceClock, errors=(500, 503),
    ),
    Operation(
        "settle_sandbox", "POST", "/v1/sandbox:settle", SANDBOX_PERMISSION, "sandbox",
        "Wait until queued deliveries reach the fabulas", models.SettleResult,
    ),
)


class SandboxApi:
    def __init__(self, sandbox: Sandbox, events: EventDirectory):
        self.sandbox = sandbox
        self.event_types = events

    async def get_sandbox(self, actor: Actor, path, query: None, body: None):
        return self.sandbox.status()

    async def list_sandbox_invocations(self, actor: Actor, path, query: models.InvocationQuery, body: None):
        return models.InvocationList(items=tuple(self.sandbox.invocations(query)))

    async def complete_sandbox_invocation(self, actor: Actor, path, query: None, body: models.CompleteInvocation):
        return await self.sandbox.complete(body.invocation_id, body.outcome)

    async def list_sandbox_events(self, actor: Actor, path, query: models.EventQuery, body: None):
        return models.SandboxEventList(items=tuple(self.sandbox.events(query)))

    async def publish_sandbox_event(self, actor: Actor, path, query: None, body: models.PublishEvent):
        if self.event_types.resolve(body.type) is None:
            raise Unprocessable(f"the catalog has no event type {body.type}", code="unknown_event_type")
        event = Event(
            id=body.id or f"sandbox-{uuid.uuid4().hex}",
            type=body.type,
            source=body.source,
            subject=body.subject,
            time=self.sandbox.clock.now(),
            data=body.data,
            attributes=body.attributes,
        )
        return await self.sandbox.publish(event)

    async def advance_sandbox_clock(self, actor: Actor, path, query: None, body: models.AdvanceClock):
        return await self.sandbox.advance(body.by)

    async def settle_sandbox(self, actor: Actor, path, query: None, body: None):
        return await self.sandbox.settle()
