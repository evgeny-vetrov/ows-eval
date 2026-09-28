"""Every failure is an RFC 7807 problem, traced, and never leaks internals."""

import asyncio

from ai.gena.services.fabula.manager.testing.scenarios import V1, order
from ai.gena.services.fabula.server.app import create_app
from ai.gena.services.fabula.server.composition import build
from ai.gena.services.fabula.server.middleware import TRACE_HEADER
from ai.gena.services.fabula.server.testing.harness import make_settings, running

PROBLEM = "application/problem+json"
ORDER = "/v1/scenarios/test/order"


def run(coroutine):
    return asyncio.run(coroutine)


def test_bodies_must_be_json_and_not_too_large():
    async def scenario():
        async with running(make_settings(http={"max_body_bytes": 1024})) as s:
            broken = await s.call("POST", "/v1/scenarios:validate", content=b"{nope", headers={"content-type": "application/json"}, expect=400)
            assert broken.json()["code"] == "invalid_json" and broken.headers["content-type"] == PROBLEM
            form = await s.call("POST", "/v1/scenarios:validate", content=b"a=1", headers={"content-type": "application/x-www-form-urlencoded"}, expect=415)
            assert form.json()["code"] == "unsupported_media_type"
            large = await s.call("POST", "/v1/scenarios:validate", json={"source": "x" * 2000}, expect=413)
            assert large.json()["code"] == "body_too_large"

            async def chunks():
                for _ in range(3):
                    yield b" " * 500

            streamed = await s.call("POST", "/v1/scenarios:validate", content=chunks(), headers={"content-type": "application/json"}, expect=413)
            assert streamed.json()["code"] == "body_too_large"
            # A cross-site form or no-cors fetch cannot say it sends JSON: nothing else is read.
            untyped = await s.call("POST", "/v1/scenarios:validate", content=b'{"source": "do: []"}', expect=415)
            assert untyped.json()["code"] == "unsupported_media_type" and untyped.headers["x-fabula-operation"] == "validateScenario"
            nan = await s.call("POST", "/v1/sandbox/events", content=b'{"type": "test.ping", "data": {"x": NaN}}', headers={"content-type": "application/json"}, expect=400)
            assert nan.json()["code"] == "invalid_json"
            empty = await s.call("POST", "/v1/fabulas:reconcile", expect=200)
            assert empty.json() == {"repaired": 0}, "no body needs no type"

    run(scenario())


def test_the_managers_errors_keep_their_status_and_code():
    async def scenario():
        async with running() as s:
            assert (await s.call("GET", ORDER, expect=404)).json()["code"] == "scenario_not_found"
            invalid = (await s.call("POST", ORDER + "/versions", json={"request_id": "v", "source": order("do:\n  - a: {wait: P1M}\n", "1.0.0")}, expect=422)).json()
            assert invalid["code"] == "invalid_scenario" and invalid["diagnostics"][0]["code"] == "duration.invalid"
            await s.post(ORDER + "/versions", {"request_id": "v1", "source": order(V1, "1.0.0")}, expect=201)
            clash = await s.call("POST", ORDER + "/versions", json={"request_id": "v1b", "source": order(V1.replace("version: 1", "version: 7"), "1.0.0")}, expect=409)
            assert clash.json()["code"] == "version_exists"
            schema = (await s.call("POST", "/v1/fabulas", json={"scenario": "test/order"}, expect=400)).json()
            assert schema["code"] == "invalid_input" and schema["errors"][0]["loc"] == ["request_id"]
            assert (await s.call("GET", "/v1/fabulas", params={"nope": "1"}, expect=400)).json()["code"] == "invalid_input"
            assert (await s.call("GET", ORDER + "/tags", params={"x": "1"}, expect=400)).json()["code"] == "unexpected_query"
            assert (await s.call("GET", "/v1/fabulas/f-nope", expect=404)).headers["content-type"] == PROBLEM

    run(scenario())


def test_a_failure_inside_is_a_500_with_the_trace_id_only():
    async def scenario():
        async with running() as s:
            async def explode(*args, **kwargs):
                raise RuntimeError("secret connection string")

            s.components.manager.registry.list_scenarios = explode
            response = await s.call("GET", "/v1/scenarios", headers={TRACE_HEADER: "trace-42"}, expect=500)
            problem = response.json()
            assert problem["code"] == "internal" and problem["trace_id"] == "trace-42" == response.headers[TRACE_HEADER]
            assert "secret" not in response.text

    run(scenario())


def test_every_response_carries_a_trace_id():
    async def scenario():
        async with running() as s:
            given = await s.call("GET", "/v1/me", headers={TRACE_HEADER: "from-the-web-1"})
            assert given.headers[TRACE_HEADER] == "from-the-web-1"
            invalid = await s.call("GET", "/v1/me", headers={TRACE_HEADER: "bad id with spaces"})
            assert invalid.headers[TRACE_HEADER] != "bad id with spaces" and len(invalid.headers[TRACE_HEADER]) == 32
            assert TRACE_HEADER.lower() in (await s.call("GET", "/nowhere", expect=404)).headers

    run(scenario())


def test_cors_lets_the_configured_web_origins_in():
    async def scenario():
        async with running(make_settings(http={"cors_origins": ["http://localhost:5173"]})) as s:
            preflight = await s.call(
                "OPTIONS",
                "/v1/fabulas",
                headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type,x-fabula-actor"},
                expect=200,
            )
            assert preflight.headers["access-control-allow-origin"] == "http://localhost:5173"
            simple = await s.call("GET", "/v1/me", headers={"Origin": "http://localhost:5173"}, expect=200)
            assert "x-trace-id" in simple.headers["access-control-expose-headers"].lower()
            stranger = await s.call("GET", "/v1/me", headers={"Origin": "http://evil.example"}, expect=200)
            assert "access-control-allow-origin" not in stranger.headers
            put = await s.call(
                "OPTIONS",
                "/v1/scenarios/test/order/resolution-policy",
                headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "PUT"},
                expect=200,
            )
            assert "PUT" in put.headers["access-control-allow-methods"]

    run(scenario())


def test_readiness_follows_the_lifecycle():
    import httpx

    async def scenario():
        app = create_app(components=build(make_settings()))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            assert (await client.get("/healthz")).status_code == 200
            assert (await client.head("/healthz")).status_code == 200
            not_ready = await client.get("/readyz")
            assert not_ready.status_code == 503 and not_ready.json()["code"] == "not_ready"
            async with app.router.lifespan_context(app):
                assert (await client.get("/readyz")).json() == {"status": "ready"}
            assert (await client.get("/readyz")).status_code == 503

    run(scenario())


def test_large_answers_are_compressed():
    async def scenario():
        async with running() as s:
            response = await s.call("GET", "/openapi.json", headers={"Accept-Encoding": "gzip"}, expect=200)
            assert response.headers["content-encoding"] == "gzip" and response.json()["openapi"] == "3.1.0"

    run(scenario())


def test_a_dev_server_answers_only_to_its_own_host_names():
    async def scenario():
        async with running() as s:
            rebound = await s.call("GET", "/v1/me", headers={"Host": "evil.example:8080"}, expect=400)
            assert rebound.json()["code"] == "untrusted_host" and TRACE_HEADER.lower() in rebound.headers
            for host in ("localhost:8080", "127.0.0.1", "[::1]:8080"):
                assert (await s.call("GET", "/v1/me", headers={"Host": host})).status_code == 200, host
        async with running(make_settings(http={"allowed_hosts": ["*.example.org"]})) as s:
            assert (await s.call("GET", "/healthz", headers={"Host": "fabula.example.org"})).status_code == 200
            assert (await s.call("GET", "/healthz", expect=400)).json()["code"] == "untrusted_host"
        async with running(make_settings(auth={"mode": "tokens"})) as s:
            assert (await s.call("GET", "/healthz", headers={"Host": "anything.example"})).status_code == 200, "any host outside dev mode"

    run(scenario())


def test_the_cli_refuses_bad_settings_before_starting(tmp_path, capsys):
    from ai.gena.services.fabula.server.cli import main

    assert main(["--port", "70000"]) == 2
    assert "invalid --host or --port" in capsys.readouterr().err
    (tmp_path / "bad.yaml").write_text("auth: {mode: open}\n")
    assert main(["--config", str(tmp_path / "bad.yaml")]) == 2
    assert "auth.mode" in capsys.readouterr().err
