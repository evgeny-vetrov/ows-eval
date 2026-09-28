PY3TEST()

SIZE(SMALL)

TEST_SRCS(
    test_background.py
    test_sandbox.py
    test_stream.py
    test_tour.py
)

DATA(
    arcadia/ai/gena/services/fabula/server/examples
)

PEERDIR(
    ai/gena/services/fabula/server
)

END()
