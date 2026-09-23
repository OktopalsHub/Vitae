from app.models import Profile, ProfileExperience
from app.profile.experience import (
    create_experience,
    delete_experience,
    list_experience,
    move_experience,
    update_experience,
)


def test_experience_crud_and_order(db_session, confirmed_user):
    user = confirmed_user["user"]
    profile = db_session.get(Profile, user.active_profile_id)

    first = create_experience(
        db_session,
        user,
        profile.id,
        position="Backend Engineer",
        company="Acme",
        location="Remote",
        start_date="2023-01",
        end_date="2024-01",
        description="Built APIs.",
    )
    second = create_experience(
        db_session,
        user,
        profile.id,
        position="Senior Backend Engineer",
        company="Beta",
        start_date="2024-02",
        end_date="Present",
        description="Owned services.",
    )
    db_session.commit()

    assert [row.id for row in list_experience(db_session, user, profile.id)] == [
        first.id,
        second.id,
    ]

    move_experience(db_session, user, second.id, "up")
    db_session.commit()
    assert [row.id for row in list_experience(db_session, user, profile.id)] == [
        second.id,
        first.id,
    ]

    updated = update_experience(
        db_session,
        user,
        first.id,
        position="Staff Backend Engineer",
        company="Acme",
        description="Built and operated APIs.",
    )
    db_session.commit()
    assert updated.position == "Staff Backend Engineer"

    delete_experience(db_session, user, second.id)
    db_session.commit()
    assert [row.id for row in list_experience(db_session, user, profile.id)] == [first.id]


def test_experience_rejects_foreign_profile(db_session, confirmed_user):
    user = confirmed_user["user"]
    profile = db_session.get(Profile, user.active_profile_id)
    other = db_session.query(type(user)).filter(type(user).email != user.email).first()

    if other is None:
        other = type(user)(
            email="experience-other@example.com",
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
    db_session.commit()

    try:
        create_experience(
            db_session,
            user,
            other_profile.id,
            position="Engineer",
            company="Other",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Expected cross-profile experience creation to fail")
