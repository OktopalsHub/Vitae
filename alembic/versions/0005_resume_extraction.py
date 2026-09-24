"""Add structured extraction fields to resume versions.

Revision ID: 0005
Revises: 0004_backfill_profile
"""

from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004_backfill_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "resume_versions",
        sa.Column("parser_name", sa.String(length=64), nullable=False, server_default="vitae-cv-parser"),
    )
    op.add_column(
        "resume_versions",
        sa.Column("extracted_profile_json", sa.Text(), nullable=False, server_default="{}"),
    )
    op.create_index(
        "ix_resume_versions_status",
        "resume_versions",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_resume_versions_status", table_name="resume_versions")
    op.drop_column("resume_versions", "extracted_profile_json")
    op.drop_column("resume_versions", "parser_name")
