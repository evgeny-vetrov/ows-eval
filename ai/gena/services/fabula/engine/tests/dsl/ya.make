PY3TEST()

SIZE(SMALL)

TEST_SRCS(
    test_compiler.py
    test_parser.py
    test_validator.py
)

PEERDIR(
    ai/gena/services/fabula/engine
)

END()
