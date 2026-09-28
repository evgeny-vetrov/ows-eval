from dataclasses import dataclass
from datetime import timedelta

from ai.gena.services.fabula.engine.model.durations import MAX_DURATION


@dataclass(frozen=True)
class EngineLimits:
    """Runtime ceilings that keep the state bounded and every transition short."""

    max_for_iterations: int = 100_000
    max_steps_per_transition: int = 20_000
    max_listen_events: int = 1_000
    max_paused_stimuli: int = 1_000
    max_recent_stimuli: int = 256
    max_recent_controls: int = 64
    max_delivery_keys: int = 256
    max_duration: timedelta = MAX_DURATION
    subscription_expiry_margin: timedelta = timedelta(days=1)
