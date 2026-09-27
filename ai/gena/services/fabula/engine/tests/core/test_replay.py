"""Every scenario case, replayed with the state reloaded after each transition."""

import json

import pytest

from all_cases import ALL_CASES

from ai.gena.services.fabula.engine.core.state import FabulaState
from ai.gena.services.fabula.engine.ports.blobs import externalize, internalize
from ai.gena.services.fabula.engine.testing.runner import Case, run_case


def through_blob_store(state_json: str) -> FabulaState:
    stored, blobs = externalize(json.loads(state_json), threshold=64)
    return FabulaState.model_validate(internalize(stored, blobs.__getitem__))


@pytest.mark.parametrize("case", ALL_CASES, ids=str)
def test_replay_with_reload_is_byte_identical(case: Case):
    straight = run_case(case)
    reloaded = run_case(case, roundtrip=True)
    offloaded = run_case(case, roundtrip=through_blob_store)
    assert straight.fingerprint() == reloaded.fingerprint() == offloaded.fingerprint()


def test_case_names_are_unique():
    names = [case.name for case in ALL_CASES]
    assert len(names) == len(set(names))
