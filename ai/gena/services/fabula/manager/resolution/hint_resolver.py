"""A deterministic `ConflictResolutionAgent`: the baseline without a language model.

It maps what the diff suggests and restarts every other unmapped node in place when
the target has a node at the same path; otherwise it proposes to stay.
"""

from ai.gena.services.fabula.manager.model.resolution import ProposedResolution, ResolutionBrief


class HintResolver:
    def __init__(self, actor_id: str = "hint-resolver"):
        self.actor_id = actor_id

    async def resolve(self, brief: ResolutionBrief) -> ProposedResolution:
        mapping = {**brief.current_mapping, **brief.suggested_mapping}
        for move in brief.representative.cursor:
            if move.disposition != "unmapped" or move.old_node in mapping:
                continue
            if move.old_node not in brief.target_nodes:
                return ProposedResolution(action="stay", rationale=f"{move.old_node} has no counterpart in {brief.target_ref}")
            mapping[move.old_node] = move.old_node
        if "resolve" not in brief.allowed_actions:
            return ProposedResolution(action="stay", rationale="the policy does not allow resolving")
        return ProposedResolution(
            action="resolve",
            mapping=mapping,
            rationale="map the diff's renames and restart changed nodes in place",
            confidence=0.5,
        )
