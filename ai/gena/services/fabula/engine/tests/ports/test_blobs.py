import json

from ai.gena.services.fabula.engine.ports.blobs import externalize, internalize


def _round_trip(document, threshold):
    stored, blobs = externalize(document, threshold)
    return stored, blobs, internalize(stored, blobs.__getitem__)


def test_large_values_move_out_and_come_back():
    big = "x" * 10_000
    document = {"frames": {"/a": {"input": big, "visit": 1}}, "data": {"items": list(range(3000))}, "status": "waiting"}
    stored, blobs, restored = _round_trip(document, 1024)
    assert restored == document
    assert len(json.dumps(stored)) < 1024
    assert stored["status"] == "waiting"
    assert stored["frames"]["/a"]["visit"] == 1


def test_small_documents_are_untouched():
    document = {"a": 1, "b": [1, 2]}
    stored, blobs, restored = _round_trip(document, 1024)
    assert stored == document and blobs == {} and restored == document


def test_user_data_that_looks_like_a_reference_is_escaped():
    document = {"data": {"$blob": "sha256:nope", "size": 1}, "other": {"$literal": {"x": 1}}}
    stored, blobs, restored = _round_trip(document, 4)
    assert restored == document


def test_equal_values_share_one_blob():
    big = {"payload": "y" * 5000}
    stored, blobs, restored = _round_trip({"a": big, "b": big}, 1024)
    assert len(blobs) == 1
    assert restored == {"a": big, "b": big}


def test_externalize_is_deterministic_and_keeps_key_order():
    document = {"z": "q" * 3000, "a": {"k2": 1, "k1": "w" * 3000}}
    first = externalize(document, 512)
    second = externalize(document, 512)
    assert first == second
    assert list(internalize(first[0], first[1].__getitem__)["a"]) == ["k2", "k1"]
