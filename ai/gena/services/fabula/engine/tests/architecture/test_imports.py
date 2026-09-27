"""Dependency rules of the package, checked on the source of every module."""

import ast
import importlib
import importlib.util
import pkgutil
import sys

import pytest

import ai.gena.services.fabula.engine as engine

PACKAGE = engine.__name__
ALLOWED_THIRD_PARTY = {"pydantic", "yaml", "jsonschema"}
PURE_LAYERS = ("model", "ports", "dsl", "core")
# The pure layers get time and randomness only as inputs.
FORBIDDEN_IN_PURE = {"random", "secrets", "uuid", "time", "threading", "asyncio", "socket", "subprocess", "os", "urllib", "http"}
FORBIDDEN_CALLS = {"now", "utcnow", "today", "time", "perf_counter", "monotonic", "urandom"}


def _modules() -> dict[str, ast.Module]:
    modules = {}
    for info in pkgutil.walk_packages(engine.__path__, prefix=PACKAGE + "."):
        if ".tests" in info.name:
            continue
        spec = importlib.util.find_spec(info.name)
        source = spec.loader.get_source(info.name)
        modules[info.name] = ast.parse(source)
    return modules


MODULES = _modules()
MODULES_ARE_PACKAGES = {name: importlib.util.find_spec(name).submodule_search_locations is not None for name in MODULES}


def _imports(name: str, tree: ast.Module) -> list[str]:
    found = []
    package = name if MODULES_ARE_PACKAGES.get(name) else name.rsplit(".", 1)[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.rsplit(".", node.level - 1)[0] if node.level > 1 else package
                found.append(f"{base}.{node.module}" if node.module else base)
            else:
                found.append(node.module)
    return found


def _layer(name: str) -> str:
    rest = name[len(PACKAGE) + 1 :]
    return rest.split(".", 1)[0]


def test_walk_found_every_layer():
    assert len(MODULES) >= 40
    assert {_layer(name) for name in MODULES} >= {"model", "ports", "dsl", "core", "runtime", "testing"}


@pytest.mark.parametrize("name", sorted(MODULES))


def test_only_stdlib_pydantic_yaml_jsonschema(name):
    for imported in _imports(name, MODULES[name]):
        top = imported.split(".", 1)[0]
        if imported.startswith(PACKAGE):
            continue
        assert top in sys.stdlib_module_names or top in ALLOWED_THIRD_PARTY, f"{name} imports {imported}"


@pytest.mark.parametrize("name", sorted(n for n in MODULES if _layer(n) in PURE_LAYERS))


def test_pure_layers_do_not_reach_the_host(name):
    for imported in _imports(name, MODULES[name]):
        if imported.startswith(PACKAGE + "."):
            assert _layer(imported) not in ("runtime", "testing"), f"{name} imports {imported}"
        assert imported.split(".", 1)[0] not in FORBIDDEN_IN_PURE, f"{name} imports {imported}"


@pytest.mark.parametrize("name", sorted(n for n in MODULES if _layer(n) in PURE_LAYERS))


def test_pure_layers_do_not_read_clocks_or_entropy(name):
    for node in ast.walk(MODULES[name]):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in FORBIDDEN_CALLS, f"{name} calls .{node.func.attr}() at line {node.lineno}"


def test_layers_below_do_not_import_layers_above():
    order = {"model": 0, "ports": 1, "dsl": 2, "core": 3, "runtime": 4, "testing": 5}
    for name, tree in MODULES.items():
        layer = _layer(name)
        if layer not in order:
            continue
        for imported in _imports(name, tree):
            if imported.startswith(PACKAGE + ".") and _layer(imported) in order:
                assert order[_layer(imported)] <= order[layer], f"{name} ({layer}) imports {imported}"


def test_runtime_does_not_depend_on_the_test_kit():
    for name, tree in MODULES.items():
        if _layer(name) == "runtime":
            assert not any(_layer(i) == "testing" for i in _imports(name, tree) if i.startswith(PACKAGE + "."))


def test_the_package_imports_cleanly():
    for name in MODULES:
        importlib.import_module(name)
