import asyncio

import pytest

from ai.gena.services.fabula.engine.model.catalog import CapabilityDescriptor
from ai.gena.services.fabula.manager.model.errors import Conflict, InvalidInput, NotFound, RevisionConflict, Unprocessable
from ai.gena.services.fabula.manager.model.resolution import ResolutionPolicy, SetResolutionPolicy
from ai.gena.services.fabula.manager.model.scenarios import CreateScenario, DeprecateVersion, PublishVersion, UpdateScenario
from ai.gena.services.fabula.manager.model.tags import TRUNK, MoveTag
from ai.gena.services.fabula.manager.testing.scenarios import SCENARIO, V1, V2, order, publish
from ai.gena.services.fabula.manager.testing.stand import ALICE, Stand


def run(coroutine):
    return asyncio.run(coroutine)


def test_publishing_pins_the_version_and_creates_the_scenario_and_trunk():
    stand = Stand()

    async def scenario():
        version = await publish(stand, V1, "1.0.0")
        info = await stand.manager.registry.get_scenario(SCENARIO)
        trunk = await stand.manager.tags.get(SCENARIO, TRUNK)
        return version, info, trunk

    version, info, trunk = run(scenario())
    assert (version.ref, version.status, version.published_by) == ("test/order:1.0.0", "active", "user:alice")
    assert version.snapshot.capabilities.keys() == {"echo:1@test"}
    assert version.source == order(V1, "1.0.0")
    assert (info.owners, info.created_by) == (("alice",), "user:alice")
    assert (trunk.allocation.single_version, trunk.revision) == ("1.0.0", 1)
    assert stand.audit.actions("scenario:test/order") == ["scenario.created", "tag.create", "version.published"]
    assert stand.notifier.kinds() == ["version.published"]


def test_republishing_the_same_content_is_a_no_op():
    stand = Stand()

    async def scenario():
        first = await publish(stand, V1, "1.0.0")
        again = await stand.manager.registry.publish(ALICE, SCENARIO, PublishVersion(request_id="other", source=order(V1, "1.0.0")))
        return first, again, await stand.manager.registry.list_versions(SCENARIO)

    first, again, versions = run(scenario())
    assert again == first
    assert [v.version for v in versions.items] == ["1.0.0"]


def test_other_content_under_a_published_version_is_refused():
    stand = Stand()
    run(publish(stand, V1, "1.0.0"))
    with pytest.raises(Conflict) as error:
        run(publish(stand, V2, "1.0.0"))
    assert error.value.code == "version_exists"
    assert "pinned capability and event descriptors" in error.value.message


def test_a_catalog_change_changes_the_digest_of_the_same_text():
    stand = Stand()
    run(publish(stand, V1, "1.0.0"))
    stand.deps.capabilities.add(CapabilityDescriptor(ref="echo:1@test", effects=("none",), title="changed"))
    with pytest.raises(Conflict) as error:
        run(publish(stand, V1, "1.0.0"))
    assert error.value.code == "version_exists"


def test_invalid_documents_are_rejected_with_positioned_diagnostics():
    stand = Stand()
    source = order(V1.replace("echo:1@test", "missing:1@test"), "1.0.0")
    result = stand.manager.registry.validate(source)
    assert not result.ok
    [diagnostic] = [d for d in result.diagnostics if d.severity == "error"]
    assert (diagnostic.code, diagnostic.line) == ("call.unknown_capability", 8)
    with pytest.raises(Unprocessable) as error:
        run(stand.manager.registry.publish(ALICE, SCENARIO, PublishVersion(request_id="p", source=source)))
    assert error.value.code == "invalid_scenario"
    assert error.value.details["diagnostics"][0]["code"] == "call.unknown_capability"


def test_validation_of_a_valid_document_reports_its_ref_and_digest():
    result = Stand().manager.registry.validate(order(V1, "1.0.0"))
    assert result.ok and result.ref == "test/order:1.0.0" and result.digest.startswith("sha256:")


def test_the_document_must_declare_the_scenario_it_is_published_under():
    stand = Stand()
    with pytest.raises(InvalidInput) as error:
        run(stand.manager.registry.publish(ALICE, "test/other", PublishVersion(request_id="p", source=order(V1, "1.0.0"))))
    assert error.value.code == "scenario_mismatch"


def test_deprecation_refuses_tagged_versions_and_blocks_new_starts():
    stand = Stand()

    async def scenario():
        await publish(stand, V1, "1.0.0")
        await publish(stand, V2, "2.0.0")
        with pytest.raises(Conflict) as tagged:
            await stand.manager.registry.deprecate(ALICE, SCENARIO, "1.0.0", DeprecateVersion(request_id="d1"))
        await stand.manager.tags.move(ALICE, SCENARIO, TRUNK, MoveTag(request_id="m", version="2.0.0"))
        deprecated = await stand.manager.registry.deprecate(ALICE, SCENARIO, "1.0.0", DeprecateVersion(request_id="d2"))
        again = await stand.manager.registry.deprecate(ALICE, SCENARIO, "1.0.0", DeprecateVersion(request_id="d3"))
        with pytest.raises(Conflict) as blocked:
            await stand.manager.tags.move(ALICE, SCENARIO, "canary", MoveTag(request_id="c", version="1.0.0"))
        snapshot = await stand.world.scenarios.get("test/order:1.0.0")
        return tagged.value, deprecated, again, blocked.value, snapshot

    tagged, deprecated, again, blocked, snapshot = run(scenario())
    assert tagged.code == "version_tagged"
    assert deprecated.status == "deprecated" and again == deprecated
    assert blocked.code == "version_deprecated"
    assert snapshot is not None, "running fabulas still resolve deprecated versions"


def test_scenario_metadata_and_policy_use_optimistic_locking():
    stand = Stand()

    async def scenario():
        created = await stand.manager.registry.create_scenario(
            ALICE, CreateScenario(request_id="c", scenario=SCENARIO, title="Orders", links=("https://tracker/REQ-1",))
        )
        repeated = await stand.manager.registry.create_scenario(
            ALICE, CreateScenario(request_id="c2", scenario=SCENARIO, title="Orders", links=("https://tracker/REQ-1",))
        )
        updated = await stand.manager.registry.update_scenario(ALICE, SCENARIO, UpdateScenario(request_id="u", description="How orders ship", expected_revision=1))
        with pytest.raises(RevisionConflict):
            await stand.manager.registry.update_scenario(ALICE, SCENARIO, UpdateScenario(request_id="u2", title="x", expected_revision=1))
        policy = await stand.manager.registry.set_policy(ALICE, SCENARIO, SetResolutionPolicy(request_id="p", policy=ResolutionPolicy(mode="agent_proposes")))
        with pytest.raises(Conflict):
            await stand.manager.registry.create_scenario(ALICE, CreateScenario(request_id="c3", scenario=SCENARIO, title="Other"))
        return created, repeated, updated, policy

    created, repeated, updated, policy = run(scenario())
    assert repeated == created
    assert (updated.description, updated.revision) == ("How orders ship", 2)
    assert (policy.resolution_policy.mode, policy.revision) == ("agent_proposes", 3)


def test_unknown_things_are_not_found():
    stand = Stand()
    with pytest.raises(NotFound):
        run(stand.manager.registry.get_scenario("test/none"))
    run(publish(stand, V1, "1.0.0"))
    with pytest.raises(NotFound):
        run(stand.manager.registry.get_version(SCENARIO, "9.9.9"))


def test_catalogs_are_browsable_for_editors():
    stand = Stand()
    capabilities = run(stand.manager.registry.capabilities("echo"))
    events = run(stand.manager.registry.event_types("vcs."))
    assert [c.ref for c in capabilities] == ["echo:1@test"]
    assert [e.type for e in events] == ["vcs.pr.closed", "vcs.pr.merged"]
