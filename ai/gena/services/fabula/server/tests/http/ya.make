PY3TEST()

SIZE(SMALL)

TEST_SRCS(
    test_http_auth.py
    test_http_errors.py
    test_http_routes.py
    test_server_openapi.py
)

DATA(
    arcadia/ai/gena/services/fabula/server/openapi.json
)

PEERDIR(
    ai/gena/services/fabula/server
)

END()
