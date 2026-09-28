"""Dependency rules of the manager, checked on the source of every module."""

import ast
import importlib
import importlib.util
import pkgutil
import sys

import pytest

import ai.gena.services.fabula.engine as engine
import ai.gena.services.fabula.manager as manager

PACKAGE = manager.__name__
ENGINE = engine.__name__
ALLOWED_THIRD_PARTY = {"pydantic", "pydantic_core", "yaml", "jsonschema"}
# Layers inside the manager, lowest first; a layer imports only itself and lower ones.
ORDER = {
    "model": 0,
    "ports": 1,
    "system": 1,
    "deps": 2,
    "diff": 3,
    "registry": 3,
    "fabulas": 4,
    "migrations": 5,
    "resolution": 6,
    "agents": 7,
    "service": 7,
    "api": 8,
    "testing": 9,
}


def _walk(package) -> dict[str, ast.Module]:
    modules = {}
    for info in pkgutil.walk_packages(package.__path__, prefix=package.__name__ + "."):
        if ".tests" in info.name:
            continue
        modules[info.name] = ast.parse(importlib.util.find_spec(info.name).loader.get_source(info.name))
    return modules


MODULES = _walk(manager)
ENGINE_MODULES = _walk(engine)


def _imports(tree: ast.Module) -> list[str]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative imports are not used"
            found.append(node.module)
    return found


def _layer(name: str) -> str:
    return name[len(PACKAGE) + 1 :].split(".", 1)[0]


def test_walk_found_every_layer():
    assert {_layer(name) for name in MODULES} >= set(ORDER)


@pytest.mark.parametrize("name", sorted(MODULES))
def test_only_stdlib_pydantic_yaml_jsonschema_and_the_engine(name):
    for imported in _imports(MODULES[name]):
        top = imported.split(".", 1)[0]
        if imported.startswith((PACKAGE, ENGINE)) or imported == "__future__":
            continue
        assert top in sys.stdlib_module_names or top in ALLOWED_THIRD_PARTY, f"{name} imports {imported}"


@pytest.mark.parametrize("name", sorted(MODULES))
def test_layers_below_do_not_import_layers_above(name):
    layer = _layer(name)
    for imported in _imports(MODULES[name]):
        if imported.startswith(PACKAGE + "."):
            assert ORDER[_layer(imported)] <= ORDER[layer], f"{name} ({layer}) imports {imported}"


@pytest.mark.parametrize("name", sorted(n for n in MODULES if _layer(n) != "testing"))
def test_only_the_test_kit_uses_the_engine_test_kit(name):
    for imported in _imports(MODULES[name]):
        assert not imported.startswith(ENGINE + ".testing"), f"{name} imports {imported}"


def test_the_engine_does_not_know_the_manager():
    for name, tree in ENGINE_MODULES.items():
        assert not any(i.startswith(PACKAGE) for i in _imports(tree) if i), f"{name} imports the manager"


def test_the_package_imports_cleanly():
    for name in MODULES:
        importlib.import_module(name)
