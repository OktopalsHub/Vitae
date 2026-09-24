from __future__ import annotations

from pathlib import Path

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


# ---------------------------------------------------------------------------
# Original CV download routes (Settings + Profiles)
# ---------------------------------------------------------------------------

def _plant_master_cv(db, user, profile) -> Path:
    from app.accounts.paths import profile_data_dir

    dest = profile_data_dir(user.id, profile.id) / "original-cv.pdf"
    dest.write_bytes(b"%PDF-1.7 fake cv")
    profile.master_cv_path = str(dest)
    db.add(profile)
    db.commit()
    return dest


def test_profile_download_cv_serves_original(client, confirmed_user, db_session):
    from app.accounts import get_active_profile

    user = confirmed_user["user"]
    profile = get_active_profile(db_session, user)
    dest = _plant_master_cv(db_session, user, profile)
    try:
        r = client.get(f"/profiles/{profile.id}/download-cv")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/pdf")
        assert 'filename="original-cv.pdf"' in r.headers.get("content-disposition", "")
        assert b"%PDF-1.7" in r.content

        # Settings route still serves the active profile's original CV.
        r2 = client.get("/settings/download-cv")
        assert r2.status_code == 200
        assert b"%PDF-1.7" in r2.content
    finally:
        dest.unlink(missing_ok=True)


def test_profile_download_cv_404_when_no_cv(client, confirmed_user, db_session):
    from app.accounts import get_active_profile

    user = confirmed_user["user"]
    profile = get_active_profile(db_session, user)
    profile.master_cv_path = ""
    db_session.add(profile)
    db_session.commit()

    r = client.get(f"/profiles/{profile.id}/download-cv")
    assert r.status_code == 404
    r2 = client.get("/settings/download-cv")
    assert r2.status_code == 404


def test_profile_download_cv_blocks_other_users(client, confirmed_user, db_session, register_user):
    from app.accounts import get_active_profile
    from app.models import User

    owner = confirmed_user["user"]
    profile = get_active_profile(db_session, owner)
    dest = _plant_master_cv(db_session, owner, profile)
    try:
        # Register a second user and verify them so the login actually switches
        # the client away from the already-authenticated owner session.
        other = register_user(email=f"other-{uuid.uuid4().hex[:8]}@example.com")
        other_user = db_session.query(User).filter(User.email == other["email"]).one()
        other_user.is_verified = True
        db_session.add(other_user)
        db_session.commit()

        login_csrf = client.cookies.get("vitae_csrf") or ""
        login = client.post(
            "/login",
            data={
                "email": other["email"],
                "password": other["password"],
                "next": "/profiles",
                "csrf_token": login_csrf,
            },
            follow_redirects=False,
        )
        assert login.status_code in {302, 303}
        r = client.get(f"/profiles/{profile.id}/download-cv", follow_redirects=False)
        assert r.status_code == 404
    finally:
        dest.unlink(missing_ok=True)
