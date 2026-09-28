"""The agent that resolves migration conflicts, behind a port.

An implementation calls whatever agent platform is available (for example an agent
API); `resolution.hint_resolver.HintResolver` is a deterministic implementation.
Contract: `resolve` returns a proposal for the brief and has no side effects; the
manager dry-runs and checks every proposal against the policy before anything happens.
"""

from typing import Protocol

from ai.gena.services.fabula.manager.model.resolution import ProposedResolution, ResolutionBrief


class ConflictResolutionAgent(Protocol):
    @property
    def actor_id(self) -> str: ...

    async def resolve(self, brief: ResolutionBrief) -> ProposedResolution: ...
