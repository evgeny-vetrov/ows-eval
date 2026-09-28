"""Scenarios published when the server starts, so a fresh in-memory server has
something to show. Publishing is idempotent: the request id is the text's digest."""

import hashlib
import logging
from pathlib import Path

import yaml

from ai.gena.services.fabula.manager.model.common import Actor, scenario_id
from ai.gena.services.fabula.manager.model.errors import ManagerError
from ai.gena.services.fabula.manager.model.scenarios import PublishVersion
from ai.gena.services.fabula.manager.registry.scenarios import ScenarioRegistry

log = logging.getLogger(__name__)

SEED_ACTOR = Actor(id="seed", kind="service")
SUFFIXES = (".yaml", ".yml", ".json")


class SeedError(Exception):
    pass


def _scenario_of(path: Path, text: str) -> str:
    try:
        document = (yaml.safe_load(text) or {}).get("document") or {}
        return scenario_id(document["namespace"], document["name"])
    except (yaml.YAMLError, AttributeError, KeyError, TypeError):
        raise SeedError(f"{path}: no document.namespace and document.name") from None


async def seed_scenarios(registry: ScenarioRegistry, directory: Path) -> list[str]:
    """Publish every document under `directory` in path order; returns the refs."""
    if not directory.is_dir():
        raise SeedError(f"{directory} is not a directory")
    refs = []
    for path in sorted(p for p in directory.rglob("*") if p.suffix in SUFFIXES and p.is_file()):
        text = path.read_text(encoding="utf-8")
        request = PublishVersion(request_id=f"seed.{hashlib.sha256(text.encode()).hexdigest()[:40]}", source=text, notes=f"seeded from {path.name}")
        try:
            version = await registry.publish(SEED_ACTOR, _scenario_of(path, text), request)
        except ManagerError as exc:
            details = "".join(f"\n  {d}" for d in exc.details.get("diagnostics", ()))
            raise SeedError(f"{path}: {exc.message}{details}") from None
        log.info("seeded %s from %s", version.ref, path)
        refs.append(version.ref)
    return refs
