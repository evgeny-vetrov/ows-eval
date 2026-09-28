import re
from datetime import datetime, timedelta, timezone
from typing import Any

MAX_DURATION = timedelta(days=180)

_ISO_DURATION = re.compile(
    r"^P(?!$)(?:(?P<weeks>\d+(?:\.\d+)?)W)?(?:(?P<days>\d+(?:\.\d+)?)D)?"
    r"(?:T(?=\d)(?:(?P<hours>\d+(?:\.\d+)?)H)?(?:(?P<minutes>\d+(?:\.\d+)?)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)
_OBJECT_UNITS = ("days", "hours", "minutes", "seconds", "milliseconds")


class DurationError(ValueError):
    pass


def parse_duration(value: Any) -> timedelta:
    """Parse an OWS duration: ISO 8601 (`P3DT4H`) or `{days, hours, minutes, seconds, milliseconds}`.

    Years and months are rejected: their length depends on the calendar.
    """
    if isinstance(value, str):
        match = _ISO_DURATION.match(value)
        if not match:
            raise DurationError(f"'{value}' is not an ISO 8601 duration without years and months")
        parts = {k: float(v) for k, v in match.groupdict().items() if v is not None}
        return timedelta(
            weeks=parts.get("weeks", 0),
            days=parts.get("days", 0),
            hours=parts.get("hours", 0),
            minutes=parts.get("minutes", 0),
            seconds=parts.get("seconds", 0),
        )
    if isinstance(value, dict):
        unknown = set(value) - set(_OBJECT_UNITS)
        if unknown:
            raise DurationError(f"unknown duration units: {', '.join(sorted(unknown))}")
        if not value:
            raise DurationError("duration object is empty")
        for unit, amount in value.items():
            if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount < 0:
                raise DurationError(f"duration unit '{unit}' must be a non-negative number")
        return timedelta(**{unit: value.get(unit, 0) for unit in _OBJECT_UNITS})
    raise DurationError("duration must be an ISO 8601 string or an object")


def parse_instant(value: Any) -> datetime:
    if not isinstance(value, str):
        raise DurationError("instant must be an ISO 8601 date-time string")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        instant = datetime.fromisoformat(text)
    except ValueError as exc:
        raise DurationError(f"'{value}' is not an ISO 8601 date-time") from exc
    if instant.tzinfo is None:
        raise DurationError(f"'{value}' has no timezone offset")
    return instant.astimezone(timezone.utc)


def format_duration(value: timedelta) -> str:
    total_ms = round(value.total_seconds() * 1000)
    days, rest = divmod(total_ms, 86_400_000)
    hours, rest = divmod(rest, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    text = "P"
    if days:
        text += f"{days}D"
    if hours or minutes or seconds or millis or not days:
        text += "T"
        if hours:
            text += f"{hours}H"
        if minutes:
            text += f"{minutes}M"
        if seconds or millis or not (hours or minutes):
            text += f"{seconds}.{millis:03d}S" if millis else f"{seconds}S"
    return text
