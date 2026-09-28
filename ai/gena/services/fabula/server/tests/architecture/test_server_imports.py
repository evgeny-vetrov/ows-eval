"""Dependency rules of the server, checked on the source of every module."""

import ast
import importlib.util
import pkgutil
import sys

import pytest

import ai.gena.services.fabula.engine as engine
import ai.gena.services.fabula.manager as manager
import ai.gena.services.fabula.server as server

PACKAGE = server.__name__
# Modules that speak HTTP; the rest of the server does not know FastAPI.
HTTP_MODULES = {"app", "routes", "errors", "middleware", "stream", "service_routes", "dependencies"}
# In-memory implementations from `testing` packages belong to the in-memory profile.
TESTING_USERS = {"composition", "sandbox", "testing"}
THIRD_PARTY = {
    "pydantic": None,
    "yaml": None,
    "fastapi": HTTP_MODULES,
    "starlette": HTTP_MODULES,
    "uvicorn": {"cli"},
    "httpx": {"testing"},
}


def _walk(package) -> dict[str, ast.Module]:
    modules = {}
    for info in pkgutil.walk_packages(package.__path__, prefix=package.__name__ + "."):
        if ".tests" in info.name:
            continue
        modules[info.name] = ast.parse(importlib.util.find_spec(info.name).loader.get_source(info.name))
    return modules


SERVER_MODULES = _walk(server)
LIBRARY_MODULES = {**_walk(engine), **_walk(manager)}


def _imports(tree: ast.Module) -> list[str]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative imports are not used"
            found.append(node.module)
    return found


def _unit(name: str) -> str:
    return name[len(PACKAGE) + 1 :].split(".", 1)[0]


@pytest.mark.parametrize("name", sorted(SERVER_MODULES))
def test_third_party_packages_stay_where_they_belong(name):
    for imported in _imports(SERVER_MODULES[name]):
        top = imported.split(".", 1)[0]
        if imported.startswith("ai.gena.services.fabula.") or top in sys.stdlib_module_names or imported == "__future__":
            continue
        assert top in THIRD_PARTY, f"{name} imports {imported}"
        allowed = THIRD_PARTY[top]
        assert allowed is None or _unit(name) in allowed, f"{name} must not import {top}"


@pytest.mark.parametrize("name", sorted(SERVER_MODULES))
def test_in_memory_implementations_stay_in_the_in_memory_profile(name):
    for imported in _imports(SERVER_MODULES[name]):
        if ".testing" in imported and imported.startswith("ai.gena.services.fabula."):
            assert _unit(name) in TESTING_USERS, f"{name} imports {imported}"


@pytest.mark.parametrize("name", sorted(LIBRARY_MODULES))
def test_the_engine_and_the_manager_know_nothing_of_the_server(name):
    for imported in _imports(LIBRARY_MODULES[name]):
        top = imported.split(".", 1)[0]
        assert not imported.startswith(PACKAGE) and top not in {"fastapi", "starlette", "uvicorn", "httpx"}, f"{name} imports {imported}"


def test_every_unit_is_known():
    assert {_unit(name) for name in SERVER_MODULES} >= HTTP_MODULES | TESTING_USERS | {"cli", "settings", "auth", "notifications"}
