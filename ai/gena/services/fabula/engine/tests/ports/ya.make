PY3TEST()

SIZE(SMALL)

TEST_SRCS(
    test_blobs.py
    test_expressions.py
)

PEERDIR(
    ai/gena/services/fabula/engine
)

END()
