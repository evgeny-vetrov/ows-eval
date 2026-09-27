PY3TEST()

SIZE(SMALL)

TEST_SRCS(
    test_filter_matching.py
    test_values.py
)

PEERDIR(
    ai/gena/services/fabula/engine
)

END()
