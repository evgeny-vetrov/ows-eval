"""Retry delays. OWS leaves the formulas open; the chosen ones are:

    constant     d
    linear       d * n
    exponential  d * 2 ** (n - 1)

where `n` is the number of the retry (1 for the first one). Jitter adds
`from + (to - from) * u` with `u` derived from sha256 of `fabula_id|path|n`, so it is
random-looking but identical on every replay.
"""

import hashlib
from datetime import timedelta

_MAX_EXPONENT = 40


def jitter_fraction(fabula_id: str, path: str, retry: int) -> float:
    digest = hashlib.sha256(f"{fabula_id}|{path}|{retry}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def retry_delay(
    *,
    base: timedelta,
    backoff: str,
    retry: int,
    jitter_from: timedelta,
    jitter_to: timedelta,
    fabula_id: str,
    path: str,
    cap: timedelta,
) -> timedelta:
    # Seconds as floats: a large retry number must hit the cap, not overflow timedelta.
    seconds = base.total_seconds()
    if backoff == "linear":
        seconds *= retry
    elif backoff == "exponential":
        seconds *= 2 ** min(retry - 1, _MAX_EXPONENT)
    if jitter_to > jitter_from or jitter_from:
        spread = (jitter_to - jitter_from).total_seconds()
        seconds += jitter_from.total_seconds() + spread * jitter_fraction(fabula_id, path, retry)
    seconds = min(seconds, cap.total_seconds())
    return timedelta(milliseconds=round(seconds * 1000))
