"""Helpers for agents built on a language model: the brief as a prompt, the answer back
as a proposal. An adapter of `ConflictResolutionAgent` to an agent platform uses them;
nothing here calls a model."""

import json
import re

import yaml
from pydantic import ValidationError

from ai.gena.services.fabula.manager.model.resolution import ProposedResolution, ResolutionBrief

INSTRUCTIONS = """\
You resolve a conflict of a scenario migration. Running fabulas of {scenario} must move
to {target}, but the engine cannot keep them where they are. Propose one action:

* resolve — give `mapping` (old node path -> new node path) and, if the new version
  needs context keys the fabulas lack, a `context_migration` expression. A mapped node
  whose definition changed restarts at its target and its operations are cancelled;
* stay — keep the fabulas on their current version;
* cancel_fabula — only if the policy allows it and nothing else makes sense.

Allowed actions: {actions}. Every proposal is dry-run and checked against the policy
before anything happens. Answer with one JSON object matching the schema at the end.
"""


def render_brief(brief: ResolutionBrief) -> str:
    rep = brief.representative
    sections = [
        INSTRUCTIONS.format(scenario=brief.scenario, target=brief.target_ref, actions=", ".join(brief.allowed_actions)),
        f"## Fabulas\n{len(brief.fabula_ids)} fabulas on {rep.from_ref} look alike; {rep.fabula_id} is shown. Status: {rep.status}.",
        "## Where it stands\n" + "\n".join(f"- {m.old_node} ({m.kind}): {m.disposition}{' - ' + m.reason if m.reason else ''}" for m in rep.cursor),
        "## Problems\n" + "\n".join(f"- {p}" for p in brief.problems or ("none",)),
        f"## Missing context keys\n{', '.join(brief.missing_variables) or 'none'}",
        f"## Context keys\n{', '.join(rep.context_keys) or 'none'}",
        "## Current mapping\n" + _yaml(brief.current_mapping),
        "## Mapping the diff suggests\n" + _yaml(brief.suggested_mapping),
        "## Old definitions\n" + _yaml(brief.old_sources),
        "## New definitions\n" + _yaml(brief.new_sources),
        "## Nodes of the target version\n" + "\n".join(f"- {n}" for n in brief.target_nodes),
        "## Answer schema\n" + json.dumps(brief.proposal_schema, indent=1, sort_keys=True),
    ]
    if rep.context is not None:
        sections.insert(6, "## Context values\n" + _yaml(rep.context))
    return "\n\n".join(sections) + "\n"


def _yaml(value) -> str:
    return "```yaml\n" + yaml.safe_dump(value, sort_keys=True, allow_unicode=True).rstrip() + "\n```"


_JSON_OBJECT = re.compile(r"\{.*\}", re.S)


class UnparsableAnswer(ValueError):
    pass


def parse_proposal(text: str) -> ProposedResolution:
    """The first JSON object of the answer, validated as a proposal."""
    match = _JSON_OBJECT.search(text)
    if match is None:
        raise UnparsableAnswer("the answer holds no JSON object")
    try:
        return ProposedResolution.model_validate_json(match.group(0))
    except ValidationError as exc:
        raise UnparsableAnswer(f"the answer is not a valid proposal: {exc}") from exc
