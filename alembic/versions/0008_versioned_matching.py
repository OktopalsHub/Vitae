"""Persist versioned match explanations.

Revision ID: 0008
Revises: 0007
"""

from alembic import op
import sqlalchemy as sa


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("listing_match_scores")}

    if "breakdown_json" not in columns:
        op.add_column(
            "listing_match_scores",
            sa.Column("breakdown_json", sa.Text(), nullable=False, server_default="{}"),
        )
    if "algorithm_version" not in columns:
        op.add_column(
            "listing_match_scores",
            sa.Column("algorithm_version", sa.String(32), nullable=False, server_default="v2"),
        )
        op.create_index(
            "ix_listing_match_scores_algorithm_version",
            "listing_match_scores",
            ["algorithm_version"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index(
        "ix_listing_match_scores_algorithm_version",
        table_name="listing_match_scores",
    )
    op.drop_column("listing_match_scores", "algorithm_version")
    op.drop_column("listing_match_scores", "breakdown_json")
