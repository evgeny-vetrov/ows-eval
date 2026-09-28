"""Server settings: one YAML file, relative paths resolved against it, and a few
environment overrides for what differs between machines (`FABULA_SERVER_HOST`,
`FABULA_SERVER_PORT`)."""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ai.gena.services.fabula.engine.model.catalog import CapabilityDescriptor, EventTypeDescriptor
from ai.gena.services.fabula.engine.model.jsonvalue import Json


class _Section(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class HttpSettings(_Section):
    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=0, le=65535)
    # Prefix the server is mounted under behind a proxy.
    root_path: str = ""
    # Origins of the web UI allowed by CORS; empty means same-origin only.
    cors_origins: tuple[str, ...] = ()
    max_body_bytes: int = Field(default=4 * 1024 * 1024, ge=1024)
    # Swagger UI at /docs and ReDoc at /redoc.
    docs: bool = True
    # Host names (globs) the server answers to; a guard against DNS rebinding. By
    # default loopback names in dev mode, any host otherwise.
    allowed_hosts: tuple[str, ...] | None = None
    # How long open requests (event streams) may finish when the server stops.
    shutdown_seconds: float = Field(default=5, gt=0)

    def trusted_hosts(self, auth_mode: str) -> tuple[str, ...]:
        if self.allowed_hosts is not None:
            return self.allowed_hosts
        return ("localhost", "127.0.0.1", "::1") if auth_mode == "dev" else ("*",)


class TokenSettings(_Section):
    """A bearer token known by its SHA-256 (hex); the token itself never sits in a file."""

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    actor: str


class AuthSettings(_Section):
    # `dev` trusts the `X-Fabula-Actor` header: for local development only.
    mode: Literal["tokens", "dev"] = "tokens"
    dev_actor: str = "user:developer"
    tokens: tuple[TokenSettings, ...] = ()
    # Role definitions added to (or replacing) the built-in ones: role -> permissions.
    roles: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    # Actor pattern (`user:alice`, `agent:*`, `*`) -> roles. Nobody has rights by default.
    grants: dict[str, tuple[str, ...]] = Field(default_factory=dict)


class CatalogSettings(_Section):
    """Capabilities and event types scenarios may use: YAML files of the form
    `{capabilities: [...], events: [...]}` and inline entries."""

    files: tuple[Path, ...] = ()
    capabilities: tuple[CapabilityDescriptor, ...] = ()
    events: tuple[EventTypeDescriptor, ...] = ()


class BehaviourSettings(_Section):
    """What a simulated capability does with an invocation.

    `manual` waits for `POST /v1/sandbox/invocations:complete`; `echo` returns the
    input; `ok` returns `output`; `error` fails with `error_type`. With `after_seconds`
    the result arrives that much later (in real time).
    """

    mode: Literal["manual", "echo", "ok", "error"] = "manual"
    output: Json = None
    error_type: Literal["runtime", "communication", "timeout", "validation", "authorization"] = "runtime"
    error_title: str = "sandbox error"
    after_seconds: float = Field(default=0, ge=0, le=24 * 3600)


class SandboxSettings(_Section):
    # Capability ref or glob (`*@demo`) -> behaviour; exact refs win, then globs in order.
    behaviours: dict[str, BehaviourSettings] = Field(default_factory=dict)
    # Expose /v1/sandbox (complete invocations, publish events, move the clock).
    expose_api: bool = True
    tick_seconds: float = Field(default=0.5, gt=0, le=60)
    settle_timeout_seconds: float = Field(default=10, gt=0)
    delivery_attempts: int = Field(default=3, ge=1, le=10)


class SeedSettings(_Section):
    # `*.yaml` / `*.yml` / `*.json` documents published at start-up, in name order.
    scenarios_dir: Path | None = None


class AgentSettings(_Section):
    """A conflict-resolution agent run inside the server; `hint` is the deterministic
    resolver that follows the diff's mapping hints."""

    actor_id: str
    resolver: Literal["hint"] = "hint"
    pool: str = "default"
    interval_seconds: float = Field(default=5, gt=0)
    max_tasks: int = Field(default=10, ge=1, le=100)


class BackgroundSettings(_Section):
    # Repair stale index records and stuck starts; 0 turns it off.
    reconcile_seconds: float = Field(default=30, ge=0)
    agents: tuple[AgentSettings, ...] = ()


class StreamSettings(_Section):
    # Notifications kept for clients that reconnect with Last-Event-ID.
    buffer: int = Field(default=1000, ge=1)
    # Notifications a slow client may lag behind before it gets `reset`.
    client_queue: int = Field(default=1000, ge=1)
    heartbeat_seconds: float = Field(default=15, gt=0)


class ServerSettings(_Section):
    # The only storage today; the ports stay the same for a database later.
    storage: Literal["memory"] = "memory"
    http: HttpSettings = HttpSettings()
    auth: AuthSettings = AuthSettings()
    catalog: CatalogSettings = CatalogSettings()
    sandbox: SandboxSettings = SandboxSettings()
    seed: SeedSettings = SeedSettings()
    background: BackgroundSettings = BackgroundSettings()
    stream: StreamSettings = StreamSettings()

    @field_validator("background")
    @classmethod
    def _unique_agents(cls, value: BackgroundSettings) -> BackgroundSettings:
        ids = [agent.actor_id for agent in value.agents]
        if len(ids) != len(set(ids)):
            raise ValueError("agents need distinct actor_id")
        return value


class SettingsError(Exception):
    pass


def _resolve(base: Path, value: Any) -> Any:
    return str(base / value) if isinstance(value, str) and not Path(value).is_absolute() else value


def _resolve_paths(data: dict[str, Any], base: Path) -> dict[str, Any]:
    catalog = data.get("catalog")
    if isinstance(catalog, dict) and isinstance(catalog.get("files"), list):
        catalog["files"] = [_resolve(base, f) for f in catalog["files"]]
    seed = data.get("seed")
    if isinstance(seed, dict) and "scenarios_dir" in seed:
        seed["scenarios_dir"] = _resolve(base, seed["scenarios_dir"])
    return data


def load_settings(path: str | Path | None = None, environ: Mapping[str, str] = os.environ) -> ServerSettings:
    data: dict[str, Any] = {}
    if path is not None:
        path = Path(path)
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise SettingsError(f"cannot read {path}: {exc}") from None
        if loaded is not None and not isinstance(loaded, dict):
            raise SettingsError(f"{path}: the top level must be a mapping")
        data = _resolve_paths(loaded or {}, path.parent)
    http = dict(data.get("http") or {})
    if "FABULA_SERVER_HOST" in environ:
        http["host"] = environ["FABULA_SERVER_HOST"]
    if "FABULA_SERVER_PORT" in environ:
        http["port"] = environ["FABULA_SERVER_PORT"]
    if http:
        data["http"] = http
    try:
        return ServerSettings.model_validate(data)
    except ValidationError as exc:
        where = f" in {path}" if path is not None else ""
        lines = [f"  {'.'.join(str(p) for p in error['loc'])}: {error['msg']}" for error in exc.errors()]
        raise SettingsError(f"invalid settings{where}:\n" + "\n".join(lines)) from None


class CatalogFile(_Section):
    capabilities: tuple[CapabilityDescriptor, ...] = ()
    events: tuple[EventTypeDescriptor, ...] = ()


def load_catalog(settings: CatalogSettings) -> tuple[list[CapabilityDescriptor], list[EventTypeDescriptor]]:
    capabilities, events = list(settings.capabilities), list(settings.events)
    for path in settings.files:
        try:
            content = CatalogFile.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})
        except (OSError, yaml.YAMLError, ValidationError) as exc:
            raise SettingsError(f"cannot read the catalog {path}: {exc}") from None
        capabilities += content.capabilities
        events += content.events
    for kind, keys in (("capability", [c.ref for c in capabilities]), ("event type", [e.type for e in events])):
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise SettingsError(f"{kind} declared twice: {', '.join(duplicates)}")
    return capabilities, events
