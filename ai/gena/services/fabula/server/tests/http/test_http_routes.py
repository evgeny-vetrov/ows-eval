"""Every operation is reachable by its method and path, as the transport-agnostic
router would find it."""

import asyncio

import pytest

from ai.gena.services.fabula.manager.api.dispatch import Dispatcher
from ai.gena.services.fabula.manager.api.operations import OPERATIONS
from ai.gena.services.fabula.server.composition import build
from ai.gena.services.fabula.server.routes import OPERATION_HEADER, starlette_path
from ai.gena.services.fabula.server.testing.harness import make_settings, running

SAMPLES = {
    "namespace": "test",
    "name": "order",
    "version": "1.0.0",
    "tag": "trunk",
    "fabula_id": "f-1",
    "request_id": "r-1",
    "campaign_id": "mig-1",
    "task_id": "res-1",
}
SERVER_OPERATIONS = build(make_settings()).dispatcher.operations


def run(coroutine):
    return asyncio.run(coroutine)


def test_every_operation_answers_at_its_method_and_path():
    async def scenario():
        async with running() as s:
            for op in SERVER_OPERATIONS:
                body = {} if op.method not in ("GET", "HEAD") else None
                response = await s.call(op.method, op.path.format(**SAMPLES), json=body)
                assert response.headers.get(OPERATION_HEADER) == op.operation_id, (op.method, op.path, response.status_code, response.text)

    run(scenario())


@pytest.mark.parametrize(
    "method, path, expected",
    [
        ("GET", "/v1/scenarios/test/order/tags/trunk:resolve", "resolveTag"),
        ("GET", "/v1/scenarios/test/order/tags/trunk", "getTag"),
        ("GET", "/v1/fabulas/f-1/controls/mig:mig-1:f-1:1:swap", "getControlOutcome"),
        ("POST", "/v1/migrations/mig-1:apply", "applyMigration"),
        ("GET", "/v1/migrations/mig-1", "getMigration"),
        ("GET", "/v1/fabulas:count", "countFabulas"),
        ("POST", "/v1/scenarios:validate", "validateScenario"),
    ],
)
def test_routes_agree_with_the_transport_agnostic_router(method, path, expected):
    routed = Dispatcher.route(method, path)
    assert routed is not None and routed[0].operation_id == expected

    async def scenario():
        async with running() as s:
            response = await s.call(method, path, json={} if method == "POST" else None)
            assert response.headers.get(OPERATION_HEADER) == expected

    run(scenario())


@pytest.mark.parametrize(
    "method, path, status",
    [
        ("GET", "/v1/migrations/mig-1:apply", 405),
        ("GET", "/v1/fabulas/f-1/unknown", 404),
        ("DELETE", "/v1/scenarios", 405),
        ("GET", "/v1/scenarios/test/order/tags/a/b", 404),
    ],
)
def test_what_the_router_does_not_know_is_a_problem(method, path, status):
    assert Dispatcher.route(method, path) is None

    async def scenario():
        async with running() as s:
            response = await s.call(method, path)
            assert response.status_code == status and response.headers["content-type"] == "application/problem+json"
            assert OPERATION_HEADER.lower() not in response.headers

    run(scenario())


def test_a_wrong_method_lists_every_allowed_one():
    async def scenario():
        async with running() as s:
            response = await s.call("PUT", "/v1/fabulas", expect=405)
            assert set(response.headers["allow"].split(", ")) == {"GET", "HEAD", "POST"}
            assert response.json()["code"] == "method_not_allowed"

    run(scenario())


def test_head_answers_like_get_without_a_body():
    async def scenario():
        async with running() as s:
            response = await s.call("HEAD", "/v1/scenarios", expect=200)
            assert response.headers[OPERATION_HEADER] == "listScenarios" and response.content == b""

    run(scenario())


def test_route_templates():
    assert starlette_path("/v1/migrations/{campaign_id}:apply") == "/v1/migrations/{campaign_id:fabula_segment}:apply"
    assert starlette_path("/v1/fabulas/{fabula_id}/controls/{request_id}") == "/v1/fabulas/{fabula_id:fabula_segment}/controls/{request_id}"
    assert len({op.operation_id for op in SERVER_OPERATIONS}) == len(SERVER_OPERATIONS) > len(OPERATIONS)
