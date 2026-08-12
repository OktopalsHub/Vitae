from __future__ import annotations

import pytest

from app.web_helpers import (
    assert_download_under_user,
    read_upload_limited,
    validate_upload_content,
    validate_upload_filename,
)
from app.config import get_settings
import uuid


def test_validate_upload_filename_rejects_exe():
    with pytest.raises(ValueError, match="PDF or DOCX"):
        validate_upload_filename("malware.exe")


def test_validate_upload_filename_strips_path():
    assert validate_upload_filename("../../evil.pdf") == "evil.pdf"


def test_validate_upload_content_pdf_magic():
    validate_upload_content("resume.pdf", b"%PDF-1.7\n%")
    with pytest.raises(ValueError, match="valid PDF"):
        validate_upload_content("resume.pdf", b"not-a-pdf")


def test_validate_upload_content_docx_magic():
    validate_upload_content("resume.docx", b"PK\x03\x04content")
    with pytest.raises(ValueError, match="valid DOCX"):
        validate_upload_content("resume.docx", b"%PDF-1.4")


def test_read_upload_limited_rejects_oversize(monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "100")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValueError, match="too large"):
            read_upload_limited(b"x" * 200)
    finally:
        monkeypatch.delenv("MAX_UPLOAD_BYTES", raising=False)
        get_settings.cache_clear()


def test_assert_download_under_user_blocks_traversal(tmp_path, monkeypatch):
    uid = uuid.uuid4()
    user_root = tmp_path / str(uid)
    user_root.mkdir(parents=True)
    out = user_root / "outputs"
    out.mkdir()
    good = out / "cv.pdf"
    good.write_bytes(b"%PDF")
    outside = tmp_path / "other"
    outside.mkdir()
    (outside / "secret.pdf").write_bytes(b"%PDF")

    monkeypatch.setattr("app.accounts.user_data_dir", lambda _uid: user_root)

    path = assert_download_under_user(uid, str(out), "cv.pdf")
    assert path == good.resolve()

    # Filename path components are stripped — only basename is used.
    nested = assert_download_under_user(uid, str(out), "../../cv.pdf")
    assert nested.name == "cv.pdf"
    assert nested.parent == out.resolve()

    with pytest.raises(PermissionError):
        assert_download_under_user(uid, str(outside), "secret.pdf")
