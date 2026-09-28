"""The API end to end through `dispatch`, with JSON in and JSON out."""

import asyncio
import json

import pytest
from pydantic import BaseModel

from ai.gena.services.fabula.engine.model.stimuli import OutcomeOk
from ai.gena.services.fabula.manager.api.dispatch import Dispatcher
from ai.gena.services.fabula.manager.api.facade import FabulaApi
from ai.gena.services.fabula.manager.api.operations import OPERATIONS
from ai.gena.services.fabula.manager.model.common import Actor
from ai.gena.services.fabula.manager.testing.scenarios import V1, V3, finish_call, hold_calls, order
from ai.gena.services.fabula.manager.testing.stand import Stand
from ai.gena.services.fabula.manager.testing.system import AllowAll, RoleAuthorizer

ALICE = Actor(id="alice")
AGENT = Actor(id="resolver-1", kind="agent")
ORDER = {"namespace": "test", "name": "order"}


def run(coroutine):
    return asyncio.run(coroutine)


class Client:
    def __init__(self, authorizer=None):
        self.stand = Stand()
        self.stand.world.invoker.behaviours["strict:1@test"] = lambda request: OutcomeOk(output={"y": 1})
        self.dispatcher = Dispatcher(FabulaApi(self.stand.manager), authorizer or AllowAll())
        self.called: set[str] = set()

    def call(self, operation: str, actor: Actor = ALICE, expect: int | None = 200, **kwargs):
        body = kwargs.get("body")
        if body is not None:
            kwargs["body"] = json.loads(json.dumps(body))
        response = run(self.dispatcher.dispatch(operation, actor=actor, **kwargs))
        if expect is not None:
            assert response.status == expect, (operation, response.body)
        json.dumps(response.body)  # the body is plain JSON
        self.called.add(operation)
        return response.body


def test_every_operation_through_the_wire():
    c = Client()
    hold_calls(c.stand)
    source_v1, source_v3 = order(V1, "1.0.0"), order(V3, "3.0.0")

    # scenarios, catalogs, versions
    c.call("createScenario", expect=201, body={"request_id": "s", "scenario": "test/order", "title": "Orders", "links": ["REQ-1"]})
    assert c.call("listScenarios", query={"namespace": "test"})["items"][0]["title"] == "Orders"
    c.call("updateScenario", path=ORDER, body={"request_id": "u", "description": "Ships orders", "expected_revision": 1})
    c.call("setResolutionPolicy", path=ORDER, body={"request_id": "p", "policy": {"mode": "agent_proposes"}})
    assert c.call("getScenario", path=ORDER)["resolution_policy"]["mode"] == "agent_proposes"
    assert c.call("validateScenario", body={"source": source_v1})["ok"]
    assert [x["ref"] for x in c.call("listCapabilities", query={"q": "echo"})["items"]] == ["echo:1@test"]
    assert c.call("listEventTypes")["items"]
    assert c.call("publishVersion", expect=201, path=ORDER, body={"request_id": "v1", "source": source_v1})["ref"] == "test/order:1.0.0"
    c.call("publishVersion", expect=201, path=ORDER, body={"request_id": "v3", "source": source_v3})
    assert [v["version"] for v in c.call("listVersions", path=ORDER)["items"]] == ["1.0.0", "3.0.0"]
    assert c.call("getVersion", path={**ORDER, "version": "3.0.0"})["source"] == source_v3
    diff = c.call("diffVersions", path=ORDER, query={"from_version": "1.0.0", "to_version": "3.0.0"})
    assert {n["path"] for n in diff["nodes"]} == {"/ask", "/merged", "/finish"}

    # tags
    trunk = {**ORDER, "tag": "trunk"}
    assert [t["tag"] for t in c.call("listTags", path=ORDER)["items"]] == ["trunk"]
    assert c.call("getTag", path=trunk)["revision"] == 1
    assert c.call("resolveTag", path=trunk, query={"routing_key": "user-1"})["version"] == "1.0.0"
    canary = {**ORDER, "tag": "canary"}
    c.call("moveTag", path=canary, body={"request_id": "canary", "version": "3.0.0"})
    c.call(
        "allocateTag",
        path=canary,
        body={"request_id": "ab", "allocation": {"arms": [{"arm": "a", "version": "1.0.0", "weight": 5000}, {"arm": "b", "version": "3.0.0", "weight": 5000}]}},
    )
    c.call("rollbackTag", path=canary, body={"request_id": "rb", "to_revision": 1})
    assert [h["kind"] for h in c.call("getTagHistory", path=canary)["items"]] == ["create", "allocate", "rollback"]

    # fabulas
    started = c.call("startFabula", expect=201, body={"request_id": "o-1", "scenario": "test/order", "input": {"ticket": "T-1"}, "labels": {"team": "a"}})
    fabula = {"fabula_id": started["fabula_id"]}
    assert c.call("startFabula", expect=200, body={"request_id": "o-1", "scenario": "test/order", "input": {"ticket": "T-1"}, "labels": {"team": "a"}})["created"] is False
    c.call("startFabula", expect=201, body={"request_id": "o-2", "scenario": "test/order", "input": {"ticket": "T-2"}})
    page = c.call("searchFabulas", query={"scenario": "test/order", "status": ["waiting"], "label": ["team=a"], "page_size": "10"})
    assert [r["fabula_id"] for r in page["items"]] == [started["fabula_id"]]
    assert c.call("countFabulas", query={"scenario": "test/order"})["items"] == [{"scenario": "test/order", "version": "1.0.0", "status": "waiting", "count": 2}]
    assert c.call("getFabula", path=fabula)["view"]["current_nodes"] == ["/ask"]
    assert [s["node"] for s in c.call("getFabulaJournal", path=fabula)["steps"]] == ["/prepare", "/ask"]
    ack = c.call("controlFabula", expect=202, path=fabula, body={"request_id": "hold", "action": {"type": "pause"}, "reason": "check"})
    assert ack["outcome"]["accepted"]
    c.call("controlFabula", expect=202, path=fabula, body={"request_id": "go", "action": {"type": "resume"}})
    assert c.call("getControlOutcome", path={**fabula, "request_id": "hold"})["action"] == "pause"
    report = c.call("analyzeFabulaSwap", path=fabula, body={"version": "3.0.0", "mapping": {"/ask": "/ask"}})
    assert report["applicable"] and report["restarts"] == ["/ask"]
    assert c.call("reconcileFabula", path=fabula)["status"] == "waiting"
    assert c.call("importFabula", path=fabula, body={"request_id": "imp"})["origin"] == "manager"
    assert c.call("reconcileFabulas")["repaired"] == 0

    # migrations and their conflicts
    campaign = c.call("createMigration", expect=201, body={"request_id": "m", "scenario": "test/order", "target_version": "3.0.0", "context_migration": '${ . + {owner: "ops"} }'})
    migration = {"campaign_id": campaign["campaign_id"]}
    assert campaign["status"] == "awaiting_decision"
    assert [m["campaign_id"] for m in c.call("listMigrations", query={"scenario": "test/order"})["items"]] == [campaign["campaign_id"]]
    assert c.call("getMigration", path=migration)["counts"] == {"state.pending": 2, "verdict.blocked": 2}
    [cluster] = c.call("listMigrationClusters", path=migration)["items"]
    assert cluster["count"] == 2 and "capability changed" in cluster["problems"][0]
    decided = c.call("decideMigration", path=migration, body={"request_id": "d", "decisions": [{"target": {"cluster_ids": [cluster["cluster_id"]]}, "action": "delegate_to_agent"}]})
    [task_id] = decided["task_ids"]
    task = {"task_id": task_id}
    assert c.call("listResolutionTasks", query={"campaign_id": campaign["campaign_id"]})["items"][0]["status"] == "open"
    [leased] = c.call("leaseResolutionTasks", actor=AGENT, body={"request_id": "l", "max_tasks": 1})["items"]
    assert leased["brief"]["problems"] == cluster["problems"]
    proposal = {"action": "resolve", "mapping": {"/ask": "/ask"}, "rationale": "restart the changed call"}
    assert c.call("dryRunResolution", actor=AGENT, path=task, body={"proposal": proposal})["acceptable"]
    assert c.call("submitResolution", actor=AGENT, path=task, body={"request_id": "sub", "proposal": proposal})["status"] == "awaiting_approval"
    assert c.call("getResolutionTask", path=task)["proposal"]["mapping"] == {"/ask": "/ask"}
    c.call("rejectResolution", path=task, body={"request_id": "no", "reason": "try again"})
    c.call("leaseResolutionTasks", actor=AGENT, body={"request_id": "l2"})
    c.call("releaseResolutionTask", actor=AGENT, path=task, body={"request_id": "rel"})
    c.call("leaseResolutionTasks", actor=AGENT, body={"request_id": "l3"})
    c.call("submitResolution", actor=AGENT, path=task, body={"request_id": "sub2", "proposal": proposal})
    assert c.call("approveResolution", path=task, body={"request_id": "yes"})["status"] == "applied"
    items = c.call("listMigrationItems", path=migration, query={"state": "migrated"})["items"]
    assert len(items) == 2
    c.call("pauseMigration", path=migration, expect=409, body={"request_id": "p"})
    assert c.call("revertMigration", path=migration, body={"request_id": "undo"})["campaign_ids"]
    back = c.call("listMigrations", query={"scenario": "test/order", "status": "awaiting_decision"})["items"][0]["campaign_id"]
    c.call("pauseMigration", path={"campaign_id": back}, body={"request_id": "p"})
    c.call("resumeMigration", path={"campaign_id": back}, body={"request_id": "r"})
    c.call("refreshMigration", path={"campaign_id": back}, body={"request_id": "f"})
    assert c.call("getMigration", path={"campaign_id": back})["counts"] == {"state.pending": 2, "verdict.blocked": 2}
    resolve = {"target": {"verdicts": ["blocked"]}, "action": "resolve", "mapping": {"/merged": "/merged"}, "approve": True}
    c.call("decideMigration", path={"campaign_id": back}, body={"request_id": "d", "decisions": [resolve]})
    applied = c.call("applyMigration", path={"campaign_id": back}, body={"request_id": "a"})
    assert applied["processed"] == {"migrated": 2}
    other = c.call("createMigration", expect=201, body={"request_id": "m2", "scenario": "test/order", "target_version": "3.0.0"})
    assert c.call("cancelMigration", path={"campaign_id": other["campaign_id"]}, body={"request_id": "x"})["status"] == "cancelled"

    # deprecation and audit
    c.call("deprecateVersion", path={**ORDER, "version": "3.0.0"}, expect=409, body={"request_id": "dep"})
    c.call("moveTag", path=canary, body={"request_id": "canary-back", "version": "1.0.0", "propose_migration": False})
    assert c.call("deprecateVersion", path={**ORDER, "version": "3.0.0"}, body={"request_id": "dep2"})["status"] == "deprecated"
    actions = [r["action"] for r in c.call("listAudit", query={"resource": f"resolution-task:{task_id}"})["items"]]
    assert actions[0] == "resolution.approved"

    assert c.called == {op.operation_id for op in OPERATIONS}, "every operation is exercised"


def test_errors_are_problems_with_stable_codes():
    c = Client()
    missing = run(c.dispatcher.dispatch("getScenario", actor=ALICE, path=ORDER))
    assert (missing.status, missing.content_type, missing.body["code"], missing.body["type"]) == (404, "application/problem+json", "scenario_not_found", "urn:fabula:problem:scenario_not_found")
    invalid = c.call("publishVersion", path=ORDER, expect=422, body={"request_id": "v", "source": order(V1.replace("echo:1@test", "nope:1@x"), "1.0.0")})
    assert invalid["diagnostics"][0]["code"] == "call.unknown_capability"
    bad = c.call("startFabula", expect=400, body={"request_id": "bad id!", "scenario": "test/order"})
    assert bad["code"] == "invalid_input" and bad["errors"][0]["loc"] == ["request_id"]
    assert c.call("getFabula", expect=400, path={})["code"] == "missing_path_parameter"
    assert c.call("listTags", path=ORDER, expect=400, query={"x": "1"})["code"] == "unexpected_query"
    assert c.call("getScenario", path=ORDER, expect=400, body={"x": 1})["code"] == "unexpected_body"
    assert c.call("searchFabulas", expect=400, query={"label": ["nokey"]})["code"] == "bad_label"
    assert c.call("searchFabulas", expect=400, query={"page_size": "0"})["code"] == "invalid_input"
    assert c.call("searchFabulas", expect=400, query={"page_token": "%%%"})["code"] == "bad_page_token"
    assert c.call("noSuchOperation", expect=404)["code"] == "unknown_operation"
    c.call("publishVersion", path=ORDER, expect=201, body={"request_id": "v", "source": order(V1, "1.0.0")})
    assert c.call("publishVersion", path=ORDER, expect=409, body={"request_id": "v2", "source": order(V3, "1.0.0")})["code"] == "version_exists"
    stale = c.call("moveTag", path={**ORDER, "tag": "trunk"}, expect=412, body={"request_id": "m", "version": "1.0.0", "expected_revision": 7})
    assert stale["code"] == "revision_mismatch"


def test_rights_are_checked_per_operation_and_resource():
    c = Client(RoleAuthorizer({"alice": {"scenario.read", "scenario.publish"}, "resolver-1": {"resolution.work"}}))
    c.call("publishVersion", path=ORDER, expect=201, body={"request_id": "v", "source": order(V1, "1.0.0")})
    denied = c.call("startFabula", expect=403, body={"request_id": "o", "scenario": "test/order"})
    assert denied["code"] == "forbidden" and "fabula.start on scenario:test/order" in denied["detail"]
    assert c.call("leaseResolutionTasks", actor=AGENT, body={"request_id": "l"})["items"] == []
    assert c.call("approveResolution", actor=AGENT, expect=403, path={"task_id": "res-1"}, body={"request_id": "a"})["code"] == "forbidden"


def test_the_router_maps_methods_and_paths_to_operations():
    samples = {"namespace": "test", "name": "order", "version": "1.0.0", "tag": "trunk", "fabula_id": "f-1", "request_id": "r-1", "campaign_id": "mig-1", "task_id": "res-1"}
    for op in OPERATIONS:
        path = op.path.format(**samples)
        routed = Dispatcher.route(op.method, path)
        assert routed is not None and routed[0] is op, (op.method, path)
        assert routed[1] == {name: samples[name] for name in op.path_params}
    assert Dispatcher.route("DELETE", "/v1/scenarios") is None
    assert Dispatcher.route("GET", "/v1/fabulas/f-1/unknown") is None


def test_an_agent_resolves_with_the_hint_resolver_against_a_started_fleet():
    """The wire and the in-process runner see the same tasks."""
    from ai.gena.services.fabula.manager.agents.runner import run_resolution_agent
    from ai.gena.services.fabula.manager.resolution.hint_resolver import HintResolver

    c = Client()
    hold_calls(c.stand)
    c.call("publishVersion", path=ORDER, expect=201, body={"request_id": "v1", "source": order(V1, "1.0.0")})
    started = c.call("startFabula", expect=201, body={"request_id": "o", "scenario": "test/order", "input": {"ticket": "T"}})
    run(finish_call(c.stand, started["fabula_id"]))
    c.call("publishVersion", path=ORDER, expect=201, body={"request_id": "v3", "source": order(V3, "3.0.0")})
    c.call("setResolutionPolicy", path=ORDER, body={"request_id": "p", "policy": {"mode": "agent_applies"}})
    moved = c.call("moveTag", path={**ORDER, "tag": "trunk"}, body={"request_id": "ship", "version": "3.0.0"})
    migration = {"campaign_id": moved["campaign_id"]}
    c.call("decideMigration", path=migration, body={"request_id": "d", "decisions": [{"target": {"verdicts": ["blocked"]}, "action": "delegate_to_agent"}]})
    runs = run(run_resolution_agent(c.stand.manager.resolution, HintResolver(actor_id=AGENT.id), run_id="r"))
    assert [r.outcome for r in runs] == ["submitted"]
    assert c.call("getMigration", path=migration)["status"] == "done"
    assert c.call("getFabula", path={"fabula_id": started["fabula_id"]})["record"]["version"] == "3.0.0"


@pytest.mark.parametrize("op", OPERATIONS, ids=lambda op: op.operation_id)
def test_every_operation_declares_its_contract(op):
    assert op.permission and op.summary and op.response is not None, "every manager operation needs a permission"
    assert (op.body is None) or op.method in ("POST", "PUT", "PATCH")
    assert op.method != "GET" or op.body is None


def test_outcomes_of_manager_generated_request_ids_are_routable():
    routed = Dispatcher.route("GET", "/v1/fabulas/f-1/controls/mig:mig-1:f-1:1:swap")
    assert routed[0].operation_id == "getControlOutcome" and routed[1]["request_id"] == "mig:mig-1:f-1:1:swap"
    assert Dispatcher.route("GET", "/v1/migrations/mig-1:apply") is None


class _Echo(BaseModel):
    actor: str = ""
    poked: str = ""


class _HostApi:
    """A host's own operations, dispatched with the manager's machinery."""

    async def whoami(self, actor, path, query, body):
        return _Echo(actor=str(actor))

    async def poke(self, actor, path, query, body):
        return _Echo(poked=path["thing"])


def test_a_host_dispatches_its_own_operations():
    from ai.gena.services.fabula.manager.api.openapi import build_openapi
    from ai.gena.services.fabula.manager.api.operations import Operation

    extra = (
        Operation("whoami", "GET", "/v1/me", None, "session", "Who am I", _Echo),
        Operation("poke", "POST", "/v1/things/{thing}:poke", "fabula.control", "session", "Poke a thing", _Echo),
    )
    nobody = RoleAuthorizer({})
    dispatcher = Dispatcher(_HostApi(), nobody, extra)
    me = run(dispatcher.dispatch("whoami", actor=ALICE))
    assert (me.status, me.body) == (200, {"actor": "user:alice", "poked": ""}), "no permission: authentication is enough"
    assert run(dispatcher.dispatch("poke", actor=ALICE, path={"thing": "x"})).status == 403
    assert run(dispatcher.dispatch("getScenario", actor=ALICE, path=ORDER)).body["code"] == "unknown_operation"
    assert dispatcher.match("POST", "/v1/things/x:poke")[0].operation_id == "poke" and Dispatcher.route("GET", "/v1/me") is None
    assert dispatcher.match("GET", "/v1/scenarios/test/order") is None
    document = build_openapi(extra, tags=[{"name": "session"}])
    assert "x-permission" not in document["paths"]["/v1/me"]["get"] and "403" not in document["paths"]["/v1/me"]["get"]["responses"]
    assert document["paths"]["/v1/things/{thing}:poke"]["post"]["x-permission"] == "fabula.control"
    with pytest.raises(ValueError, match="lacks handlers: poke"):
        Dispatcher(type("Partial", (), {"whoami": _HostApi.whoami})(), nobody, extra)
