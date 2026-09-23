from pathlib import Path

from app.models import ResumeVersion
from app.profile.cv import get_active_resume, list_resume_versions, register_cv_version


def test_register_cv_version_archives_previous(db_session, confirmed_user, tmp_path: Path):
    user = confirmed_user["user"]
    profile = db_session.query(type(user).active_profile.property.mapper.class_).get(
        user.active_profile_id
    )

    first = tmp_path / "resume-v1.pdf"
    first.write_bytes(b"%PDF-1.4 first")

    v1 = register_cv_version(
        db_session,
        user,
        profile,
        first,
        {"name": "Test User", "skills": ["Python"]},
        content_type="application/pdf",
    )
    db_session.commit()

    second = tmp_path / "resume-v2.pdf"
    second.write_bytes(b"%PDF-1.4 second")

    v2 = register_cv_version(
        db_session,
        user,
        profile,
        second,
        {"name": "Test User", "skills": ["Python", "FastAPI"]},
        content_type="application/pdf",
    )
    db_session.commit()

    db_session.refresh(v1)
    db_session.refresh(v2)

    assert v1.version == 1
    assert v1.status == "archived"
    assert v2.version == 2
    assert v2.status == "active"
    assert get_active_resume(db_session, user, profile.id).id == v2.id
    assert [v.version for v in list_resume_versions(db_session, user, profile.id)] == [2, 1]


def test_register_cv_version_rejects_foreign_profile(db_session, confirmed_user, tmp_path: Path):
    user = confirmed_user["user"]
    other = type(user)(
        email="other-resume-owner@example.com",
        hashed_password="not-a-real-password",
        is_active=True,
        is_superuser=False,
        is_verified=True,
        full_name="Other",
    )
    db_session.add(other)
    db_session.flush()

    from app.accounts.profile import create_profile

    other_profile = create_profile(db_session, other, "Other", switch_to=False)
    source = tmp_path / "resume.pdf"
    source.write_bytes(b"%PDF-1.4 test")

    try:
        register_cv_version(
            db_session,
            user,
            other_profile,
            source,
            {"name": "Other"},
            content_type="application/pdf",
        )
    except PermissionError:
        pass
    else:
        raise AssertionError("Expected cross-user CV replacement to be rejected")
