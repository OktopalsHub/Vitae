"""Add ownership constraint for the active profile."""

from alembic import op
import sqlalchemy as sa

revision = "0003_active_profile_fk"
down_revision = "0002_normalized_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.create_foreign_key(
            "fk_users_active_profile",
            "profiles",
            ["active_profile_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("fk_users_active_profile", type_="foreignkey")
