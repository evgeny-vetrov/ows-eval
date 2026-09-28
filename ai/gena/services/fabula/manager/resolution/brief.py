"""The brief of a resolution task: what the agent sees."""

from ai.gena.services.fabula.engine.core.projection import project
from ai.gena.services.fabula.engine.ports.storage import FabulaNotFound
from ai.gena.services.fabula.manager.deps import Deps
from ai.gena.services.fabula.manager.diff.scenario_diff import own_definition
from ai.gena.services.fabula.manager.diff.service import DiffService
from ai.gena.services.fabula.manager.model.campaigns import MigrationCampaign
from ai.gena.services.fabula.manager.model.common import split_ref
from ai.gena.services.fabula.manager.model.resolution import AgenticResolutionTask, ProposedResolution, RepresentativeFabula, ResolutionBrief


async def build_brief(deps: Deps, diffs: DiffService, campaign: MigrationCampaign, task: AgenticResolutionTask) -> ResolutionBrief:
    items = [i for i in [await deps.campaigns.get_item(campaign.campaign_id, f) for f in task.fabula_ids] if i is not None]
    first = next((i for i in items if i.report is not None), items[0])
    report = first.report
    source = await deps.scenarios.get_version(*split_ref(first.from_ref))
    target = await deps.scenarios.get_version(campaign.scenario, campaign.target_version)
    old_graph, new_graph = deps.graph(source.snapshot), deps.graph(target.snapshot)
    diff = diffs.between(source, target)
    mapping = first.mapping if first.mapping is not None else campaign.mapping_for(first.from_ref)
    cursor = report.cursor if report else ()
    around = {m.old_node for m in cursor} | set(mapping) | set(diff.suggested_mapping)
    wanted_new = {m.new_node for m in cursor if m.new_node} | set(mapping.values()) | set(diff.suggested_mapping.values())
    wanted_new |= {c.path for c in diff.nodes if c.change == "added"} | {m.old_node for m in cursor}
    old_sources = {p: own_definition(source.snapshot.document, old_graph, old_graph.nodes[p]) for p in sorted(around) if p in old_graph.nodes}
    new_sources = {p: own_definition(target.snapshot.document, new_graph, new_graph.nodes[p]) for p in sorted(wanted_new) if p in new_graph.nodes}
    status, waits, keys, values = "unknown", (), (), None
    try:
        state = await deps.runtime.state(first.fabula_id)
        status, waits = state.status, project(state).waits
        keys = tuple(sorted(state.data)) if isinstance(state.data, dict) else ()
        values = state.data if campaign.policy.expose_context_values else None
    except FabulaNotFound:
        pass
    return ResolutionBrief(
        task_id=task.task_id,
        campaign_id=campaign.campaign_id,
        scenario=campaign.scenario,
        target_ref=campaign.target_ref,
        fabula_ids=task.fabula_ids,
        representative=RepresentativeFabula(
            fabula_id=first.fabula_id, from_ref=first.from_ref, status=status, cursor=cursor, waits=waits, context_keys=keys, context=values
        ),
        problems=report.problems if report else (),
        missing_variables=report.missing_variables if report else (),
        current_mapping=mapping,
        suggested_mapping=diff.suggested_mapping,
        changed_nodes=tuple(c.path for c in diff.nodes),
        old_sources=old_sources,
        new_sources=new_sources,
        target_nodes=tuple(p for p in new_graph.nodes if p != "/"),
        allowed_actions=campaign.policy.allowed_actions,
        policy=campaign.policy,
        proposal_schema=ProposedResolution.model_json_schema(),
    )
