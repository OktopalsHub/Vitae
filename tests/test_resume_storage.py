from pathlib import Path

from app.resumes.storage import LocalResumeStorage


def test_resume_storage_rejects_path_escape(tmp_path):
    storage = LocalResumeStorage(tmp_path)
    try:
        storage.write("../outside.txt", b"secret")
    except ValueError:
        pass
    else:
        raise AssertionError("path traversal must be rejected")


def test_resume_storage_records_stable_checksum(tmp_path):
    storage = LocalResumeStorage(tmp_path)
    path, size, checksum = storage.write("generations/1/resume.pdf", b"resume")
    assert Path(path).exists()
    assert size == 6
    assert len(checksum) == 64
