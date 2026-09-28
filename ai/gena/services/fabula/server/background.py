"""Jobs the server runs on its own: repair of the process index and conflict-resolution
agents that work inside the server."""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable

from ai.gena.services.fabula.manager.agents.runner import run_resolution_agent
from ai.gena.services.fabula.manager.resolution.hint_resolver import HintResolver
from ai.gena.services.fabula.manager.service import Manager
from ai.gena.services.fabula.server.settings import AgentSettings, BackgroundSettings

log = logging.getLogger(__name__)


def agent_job(manager: Manager, settings: AgentSettings) -> Callable[[], Awaitable[object]]:
    agent = HintResolver(actor_id=settings.actor_id)

    async def job() -> object:
        return await run_resolution_agent(manager.resolution, agent, pool=settings.pool, max_tasks=settings.max_tasks, run_id=uuid.uuid4().hex)

    return job


class Background:
    def __init__(self, manager: Manager, settings: BackgroundSettings):
        self.jobs: dict[str, tuple[float, Callable[[], Awaitable[object]]]] = {}
        if settings.reconcile_seconds > 0:
            self.jobs["reconcile"] = (settings.reconcile_seconds, manager.fabulas.reconcile_stale)
        for agent in settings.agents:
            self.jobs[f"agent:{agent.actor_id}"] = (agent.interval_seconds, agent_job(manager, agent))
        self._tasks: list[asyncio.Task] = []

    def start(self) -> None:
        for name, (interval, job) in self.jobs.items():
            self._tasks.append(asyncio.create_task(self._every(name, interval, job), name=f"background-{name}"))

    async def stop(self) -> None:
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    async def _every(name: str, interval: float, job: Callable[[], Awaitable[object]]) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await job()
            except asyncio.CancelledError:
                raise
            except Exception:  # the next round may succeed; the loop outlives failures
                log.exception("background job %s failed", name)
