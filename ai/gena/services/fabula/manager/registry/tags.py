"""Tags: move, A/B allocation, rollback, history and routing of new starts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ai.gena.services.fabula.manager.deps import Deps
from ai.gena.services.fabula.manager.model.common import Actor
from ai.gena.services.fabula.manager.model.errors import AlreadyExists, InvalidInput, NotFound, RevisionConflict
from ai.gena.services.fabula.manager.model.fabulas import Selector
from ai.gena.services.fabula.manager.model.scenarios import ScenarioVersion
from ai.gena.services.fabula.manager.model.tags import (
    TRUNK,
    Allocation,
    Assignment,
    MoveTag,
    RollbackTag,
    SetAllocation,
    TagChange,
    TagChangeKind,
    TagHistory,
    TagList,
    TagMoveResult,
    TagState,
    check_tag,
)
from ai.gena.services.fabula.manager.registry.routing import bucket_of, pick_arm

# (actor, scenario, tag, version, request_id) -> campaign id or None
Proposer = Callable[[Actor, str, str, str, str], Awaitable[str | None]]


async def write_tag(
    deps: Deps,
    actor: Actor,
    scenario: str,
    tag: str,
    allocation: Allocation,
    kind: TagChangeKind,
    reason: str,
    request_id: str,
    expected_revision: int | None,
) -> TagState:
    now = deps.clock.now()
    revision = 1 if expected_revision is None else expected_revision + 1
    state = TagState(scenario=scenario, tag=tag, allocation=allocation, revision=revision, updated_at=now, updated_by=str(actor), reason=reason)
    change = TagChange(scenario=scenario, tag=tag, revision=revision, kind=kind, allocation=allocation, actor=str(actor), reason=reason, at=now, request_id=request_id)
    try:
        await deps.tags.save(state, change, expected_revision)
    except AlreadyExists:
        raise RevisionConflict(f"tag {tag} was created concurrently", code="revision_mismatch") from None
    await deps.audit(actor, f"tag.{kind}", f"scenario:{scenario}", request_id, tag=tag, revision=revision, versions=list(allocation.versions))
    return state


class TagService:
    def __init__(self, deps: Deps, active_version: Callable[[str, str], Awaitable[ScenarioVersion]]):
        self.deps = deps
        self.active_version = active_version
        self.proposer: Proposer | None = None

    async def get(self, scenario: str, tag: str) -> TagState:
        state = await self.deps.tags.get(scenario, tag)
        if state is None:
            raise NotFound(f"scenario {scenario} has no tag '{tag}'", code="tag_not_found")
        return state

    async def list(self, scenario: str) -> TagList:
        return TagList(items=tuple(await self.deps.tags.list(scenario)))

    async def history(self, scenario: str, tag: str) -> TagHistory:
        await self.get(scenario, tag)
        return TagHistory(items=tuple(await self.deps.tags.history(scenario, tag)))

    async def move(self, actor: Actor, scenario: str, tag: str, request: MoveTag) -> TagMoveResult:
        return await self._change(actor, scenario, tag, Allocation.single(request.version), "move", request, request.propose_migration)

    async def allocate(self, actor: Actor, scenario: str, tag: str, request: SetAllocation) -> TagMoveResult:
        return await self._change(actor, scenario, tag, request.allocation, "allocate", request, False)

    async def rollback(self, actor: Actor, scenario: str, tag: str, request: RollbackTag) -> TagMoveResult:
        if request.to_version is not None:
            allocation = Allocation.single(request.to_version)
        else:
            past = [c for c in await self.deps.tags.history(scenario, tag) if c.revision == request.to_revision]
            if not past:
                raise NotFound(f"tag '{tag}' has no revision {request.to_revision}", code="tag_revision_not_found")
            allocation = past[0].allocation
        return await self._change(actor, scenario, tag, allocation, "rollback", request, request.propose_migration)

    async def _change(self, actor: Actor, scenario: str, tag: str, allocation: Allocation, kind: TagChangeKind, request, propose: bool) -> TagMoveResult:
        try:
            check_tag(tag)
        except ValueError as exc:
            raise InvalidInput(str(exc), code="bad_tag") from None
        target = allocation.single_version
        current = await self.deps.tags.get(scenario, tag)
        if current is not None:
            history = await self.deps.tags.history(scenario, tag)
            if any(change.request_id == request.request_id for change in history):
                # A replay also finishes the proposal an interrupted first attempt owed.
                campaign_id = None
                if propose and target is not None and self.proposer is not None:
                    campaign_id = await self.proposer(actor, scenario, tag, target, request.request_id)
                return TagMoveResult(tag=current, campaign_id=campaign_id)
            if request.expected_revision is not None and request.expected_revision != current.revision:
                raise RevisionConflict(f"tag '{tag}' is at revision {current.revision}, not {request.expected_revision}")
        elif request.expected_revision is not None:
            raise NotFound(f"scenario {scenario} has no tag '{tag}'", code="tag_not_found")
        for version in allocation.versions:
            await self.active_version(scenario, version)
        if current is not None and current.allocation == allocation:
            return TagMoveResult(tag=current)
        state = await write_tag(
            self.deps, actor, scenario, tag, allocation, kind if current else "create", request.reason, request.request_id, current.revision if current else None
        )
        await self.deps.notify("tag.moved", f"scenario:{scenario}", tag=tag, revision=state.revision, versions=list(allocation.versions))
        campaign_id = None
        if propose and target is not None and self.proposer is not None:
            campaign_id = await self.proposer(actor, scenario, tag, target, request.request_id)
        return TagMoveResult(tag=state, campaign_id=campaign_id)

    async def route(self, scenario: str, selector: Selector, routing_key: str) -> tuple[Assignment, ScenarioVersion]:
        if selector.version is not None:
            version = await self.active_version(scenario, selector.version)
            return Assignment(version=version.version, ref=version.ref), version
        tag = selector.tag or TRUNK
        state = await self.get(scenario, tag)
        bucket = bucket_of(scenario, tag, state.allocation.salt, routing_key)
        arm = pick_arm(state.allocation, bucket)
        version = await self.active_version(scenario, arm.version)
        assignment = Assignment(version=arm.version, ref=version.ref, tag=tag, tag_revision=state.revision, arm=arm.arm, bucket=bucket)
        return assignment, version
