from pathlib import Path

from app.storage import LocalObjectStorage


def test_local_object_storage_round_trip(tmp_path: Path):
    storage = LocalObjectStorage(tmp_path)
    key = "users/u1/resumes/r1.pdf"
    payload = b"resume-bytes"

    assert storage.put(key, payload, content_type="application/pdf") == key
    assert storage.exists(key)
    assert storage.read(key) == payload


def test_local_object_storage_rejects_path_escape(tmp_path: Path):
    storage = LocalObjectStorage(tmp_path)
    try:
        storage.put("../outside.txt", b"no")
    except Exception as exc:
        assert "escapes" in str(exc)
    else:
        raise AssertionError("path escape was accepted")
