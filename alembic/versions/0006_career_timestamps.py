"""Align normalized career tables with timestamped ORM models.

Revision ID: 0006
Revises: 0005
"""

from alembic import op
import sqlalchemy as sa


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("profile_experiences", "profile_education", "profile_projects"):
        op.add_column(
            table,
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        )
        op.add_column(
            table,
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        )


def downgrade() -> None:
    for table in ("profile_projects", "profile_education", "profile_experiences"):
        op.drop_column(table, "updated_at")
        op.drop_column(table, "created_at")
