"""Runs an in-process `ConflictResolutionAgent` over the same service external agents
use: lease tasks, ask the agent, submit its proposals."""

import uuid
from typing import Literal

from ai.gena.services.fabula.engine.model.base import Frozen
from ai.gena.services.fabula.manager.model.common import Actor
from ai.gena.services.fabula.manager.model.errors import ManagerError
from ai.gena.services.fabula.manager.model.resolution import LeaseTasks, ReviewProposal, SubmitProposal
from ai.gena.services.fabula.manager.ports.agents import ConflictResolutionAgent
from ai.gena.services.fabula.manager.resolution.service import ResolutionService


class AgentRun(Frozen):
    task_id: str
    outcome: Literal["submitted", "refused", "failed"]
    status: str = ""
    detail: str = ""


async def run_resolution_agent(
    resolution: ResolutionService,
    agent: ConflictResolutionAgent,
    pool: str = "default",
    max_tasks: int = 10,
    lease_seconds: int = 300,
    run_id: str | None = None,
) -> list[AgentRun]:
    actor = Actor(id=agent.actor_id, kind="agent")
    run_id = run_id or uuid.uuid4().hex
    leased = await resolution.lease(actor, LeaseTasks(request_id=f"run-{run_id}", pool=pool, max_tasks=max_tasks, lease_seconds=lease_seconds))
    runs = []
    for index, entry in enumerate(leased.items):
        task_id = entry.task.task_id
        try:
            proposal = await agent.resolve(entry.brief)
        except Exception as exc:  # the agent is outside our control; give the task back
            await resolution.release(actor, task_id, ReviewProposal(request_id=f"run-{run_id}-{index}-release", reason=f"agent failed: {exc!r}"))
            runs.append(AgentRun(task_id=task_id, outcome="failed", detail=repr(exc)))
            continue
        try:
            task = await resolution.submit(actor, task_id, SubmitProposal(request_id=f"run-{run_id}-{index}", proposal=proposal))
            runs.append(AgentRun(task_id=task_id, outcome="submitted", status=task.status))
        except ManagerError as exc:
            await resolution.release(actor, task_id, ReviewProposal(request_id=f"run-{run_id}-{index}-release", reason=exc.message))
            runs.append(AgentRun(task_id=task_id, outcome="refused", detail=exc.message))
    return runs
