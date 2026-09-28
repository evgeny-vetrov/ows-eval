PY3TEST()

SIZE(SMALL)

TEST_SRCS(
    test_api.py
    test_openapi.py
)

DATA(
    arcadia/ai/gena/services/fabula/manager/openapi.json
)

PEERDIR(
    ai/gena/services/fabula/manager
)

END()
