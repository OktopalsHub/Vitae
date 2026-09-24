"""Add ownership constraint for the active profile."""

from alembic import op
import sqlalchemy as sa

revision = "0003_active_profile_fk"
down_revision = "0002_normalized_profile"
branch_labels = None
depends_on = None


def _has_fk() -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(
        fk.get("name") == "fk_users_active_profile"
        for fk in inspector.get_foreign_keys("users")
    )


def upgrade() -> None:
    if _has_fk():
        return
    with op.batch_alter_table("users") as batch:
        batch.create_foreign_key(
            "fk_users_active_profile",
            "profiles",
            ["active_profile_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    if not _has_fk():
        return
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("fk_users_active_profile", type_="foreignkey")
