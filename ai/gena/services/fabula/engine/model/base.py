from datetime import datetime, timezone
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def to_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


# All instants inside the engine are timezone-aware UTC so that serialized forms are
# identical no matter which offset the caller used.
UtcDatetime = Annotated[AwareDatetime, AfterValidator(to_utc)]
