from app.models import Profile, ProfileExperience
from app.profile.experience import add_experience, delete_experience, list_experience, update_experience


def test_experience_crud_is_profile_scoped(db_session, confirmed_user):
    user = confirmed_user["user"]
    profile = db_session.get(Profile, user.active_profile_id)

    item = add_experience(
        db_session,
        user,
        profile.id,
        position="Backend Engineer",
        company="Acme",
        location="Remote",
        start_date="2024-01",
        end_date="Present",
        description="Built APIs.",
    )
    db_session.commit()

    assert list_experience(db_session, user, profile.id)[0].id == item.id

    update_experience(
        db_session,
        user,
        item.id,
        position="Senior Backend Engineer",
        company="Acme",
        location="Remote",
        start_date="2024-01",
        end_date="Present",
        description="Built and operated APIs.",
    )
    db_session.commit()

    db_session.refresh(item)
    assert item.position == "Senior Backend Engineer"
    assert item.description == "Built and operated APIs."

    delete_experience(db_session, user, item.id)
    db_session.commit()
    assert list_experience(db_session, user, profile.id) == []


def test_experience_rejects_foreign_profile(db_session, confirmed_user):
    user = confirmed_user["user"]
    profile = db_session.get(Profile, user.active_profile_id)
    other = type(user)(
        email="experience-owner@example.com",
        hashed_password="not-a-real-password",
        is_active=True,
        is_superuser=False,
        is_verified=True,
        full_name="Other",
    )
    db_session.add(other)
    db_session.flush()

    other_profile = Profile(user_id=other.id, label="Other")
    db_session.add(other_profile)
    db_session.flush()

    try:
        add_experience(
            db_session,
            user,
            other_profile.id,
            position="Engineer",
            company="Other Co",
        )
    except PermissionError:
        pass
    else:
        raise AssertionError("Expected cross-profile experience write to be rejected")


def test_experience_requires_title_and_company(db_session, confirmed_user):
    user = confirmed_user["user"]
    profile = db_session.get(Profile, user.active_profile_id)

    try:
        add_experience(
            db_session,
            user,
            profile.id,
            position="",
            company="Acme",
        )
    except ValueError as exc:
        assert "required" in str(exc)
    else:
        raise AssertionError("Expected validation error")
