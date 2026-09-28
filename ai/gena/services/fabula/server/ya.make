PY3_LIBRARY()

PY_SRCS(
    __init__.py
    __main__.py
    app.py
    auth.py
    background.py
    cli.py
    composition.py
    dependencies.py
    errors.py
    middleware.py
    notifications.py
    openapi.py
    operations.py
    routes.py
    sandbox/__init__.py
    sandbox/api.py
    sandbox/clock.py
    sandbox/effects.py
    sandbox/models.py
    sandbox/runtime.py
    seed.py
    service_routes.py
    session.py
    settings.py
    stream.py
    testing/__init__.py
    testing/harness.py
)

PEERDIR(
    ai/gena/services/fabula/engine
    ai/gena/services/fabula/manager
    contrib/python/fastapi
    contrib/python/starlette
    contrib/python/uvicorn
    contrib/python/httpx
    contrib/python/pydantic/pydantic-2
    contrib/python/PyYAML
)

END()

RECURSE(
    bin
)

RECURSE_FOR_TESTS(
    tests
)
