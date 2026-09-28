"""`fabula-server --config server.yaml`: run the server under uvicorn."""

import argparse
import logging
import os
import sys

from pydantic import ValidationError

from ai.gena.services.fabula.server.composition import build
from ai.gena.services.fabula.server.settings import HttpSettings, SettingsError, load_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fabula-server", description="HTTP server of the fabula manager.")
    parser.add_argument("--config", default=os.environ.get("FABULA_SERVER_CONFIG"), help="settings file (YAML); FABULA_SERVER_CONFIG by default")
    parser.add_argument("--host", help="overrides http.host")
    parser.add_argument("--port", type=int, help="overrides http.port")
    parser.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = load_settings(args.config)
        overrides = {k: v for k, v in (("host", args.host), ("port", args.port)) if v is not None}
        http = HttpSettings.model_validate({**settings.http.model_dump(), **overrides})
        settings = settings.model_copy(update={"http": http})
        components = build(settings)
    except SettingsError as exc:
        print(f"fabula-server: {exc}", file=sys.stderr)
        return 2
    except ValidationError as exc:
        print(f"fabula-server: invalid --host or --port: {exc.errors()[0]['msg']}", file=sys.stderr)
        return 2

    import uvicorn

    from ai.gena.services.fabula.server.app import create_app

    # A failed start-up (a scenario that does not seed) is logged by uvicorn, which
    # then exits with a non-zero status.
    uvicorn.run(
        create_app(components=components),
        host=settings.http.host,
        port=settings.http.port,
        log_level=args.log_level,
        # TraceMiddleware writes the access log with the operation and the actor, and
        # without the query, which may hold a stream's access token.
        access_log=False,
        log_config=None,
        # Event streams never end by themselves: give them this long, then cancel.
        timeout_graceful_shutdown=settings.http.shutdown_seconds,
    )
    return 0


def run() -> None:
    """Entry point of the `fabula-server` binary."""
    sys.exit(main())
