"""Deterministic routing of new starts between the arms of a tag.

`bucket = sha256(scenario|tag|salt|routing_key) mod 10000`; the arm is found by the
cumulative weights in arm order. The same key lands in the same arm as long as the
allocation and its salt stay the same.
"""

import hashlib

from ai.gena.services.fabula.manager.model.tags import TOTAL_WEIGHT, Allocation, Arm


def bucket_of(scenario: str, tag: str, salt: str, routing_key: str) -> int:
    digest = hashlib.sha256(f"{scenario}|{tag}|{salt}|{routing_key}".encode("utf-8")).hexdigest()
    return int(digest[:16], 16) % TOTAL_WEIGHT


def pick_arm(allocation: Allocation, bucket: int) -> Arm:
    upper = 0
    for arm in allocation.arms:
        upper += arm.weight
        if bucket < upper:
            return arm
    raise ValueError(f"bucket {bucket} is outside the allocation")
