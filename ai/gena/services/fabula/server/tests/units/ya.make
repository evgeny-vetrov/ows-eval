PY3TEST()

SIZE(SMALL)

TEST_SRCS(
    test_auth_rules.py
    test_notifications.py
    test_settings.py
)

PEERDIR(
    ai/gena/services/fabula/server
)

END()
