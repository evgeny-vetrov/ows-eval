"""Semantics flags.

A behaviour change that would alter the commands of already running fabulas ships
behind a flag. Flags are copied into the state when a fabula starts, and the core reads
them only from the state, so a replay of an old fabula keeps its old behaviour.
"""

from ai.gena.services.fabula.engine.model.base import Frozen

# Order of events in the output of `listen.to.all`:
#   arrival      - as they were consumed (the original behaviour),
#   declaration  - in the order of the filters (current default).
LISTEN_ALL_ORDER = "listen_all_order"

KNOWN_FLAGS: dict[str, tuple[str, ...]] = {
    LISTEN_ALL_ORDER: ("arrival", "declaration"),
}

DEFAULT_FLAGS: dict[str, str] = {
    LISTEN_ALL_ORDER: "declaration",
}

# What a state without a flag means: the behaviour before the flag existed.
LEGACY_FLAGS: dict[str, str] = {
    LISTEN_ALL_ORDER: "arrival",
}


class Semantics(Frozen):
    engine_version: str
    flags: dict[str, str]

    def flag(self, name: str) -> str:
        return self.flags.get(name, LEGACY_FLAGS[name])


def validate_flags(flags: dict[str, str]) -> dict[str, str]:
    for name, value in flags.items():
        if name not in KNOWN_FLAGS:
            raise ValueError(f"unknown semantics flag '{name}'")
        if value not in KNOWN_FLAGS[name]:
            raise ValueError(f"flag '{name}' does not accept '{value}'")
    return dict(sorted(flags.items()))
