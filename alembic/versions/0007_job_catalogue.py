"""Add normalized job catalogue and source provenance.

Revision ID: 0007
Revises: 0006
"""

from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "job_sources" not in inspector.get_table_names():
        op.create_table(
            "job_sources",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("key", sa.String(128), nullable=False),
            sa.Column("display_name", sa.String(255), nullable=False, server_default=""),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("last_started_at", sa.DateTime(), nullable=True),
            sa.Column("last_success_at", sa.DateTime(), nullable=True),
            sa.Column("last_error_at", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
            sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_fetched_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
            sa.UniqueConstraint("key", name="uq_job_sources_key"),
        )
        op.create_index("ix_job_sources_key", "job_sources", ["key"], unique=True)

    if "job_source_records" not in inspector.get_table_names():
        op.create_table(
            "job_source_records",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("source_id", sa.Integer(), sa.ForeignKey("job_sources.id", ondelete="cascade"), nullable=False),
            sa.Column("listing_id", sa.Integer(), sa.ForeignKey("job_listings.id", ondelete="cascade"), nullable=False),
            sa.Column("external_id", sa.String(255), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("first_seen_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
            sa.Column("last_seen_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.UniqueConstraint("source_id", "external_id", name="uq_job_source_external"),
        )
        op.create_index("ix_job_source_records_source_id", "job_source_records", ["source_id"])
        op.create_index("ix_job_source_records_listing_id", "job_source_records", ["listing_id"])
        op.create_index("ix_job_source_records_last_seen_at", "job_source_records", ["last_seen_at"])
        op.create_index("ix_job_source_records_is_active", "job_source_records", ["is_active"])

    columns = _columns("job_listings")
    additions = [
        ("canonical_key", sa.String(128), ""),
        ("source_key", sa.String(128), ""),
        ("normalized_location", sa.String(255), ""),
        ("employment_type", sa.String(64), ""),
        ("remote_type", sa.String(64), ""),
        ("experience_level", sa.String(64), ""),
        ("salary_min", sa.Float(), None),
        ("salary_max", sa.Float(), None),
        ("salary_currency", sa.String(16), ""),
        ("posted_at", sa.DateTime(), None),
        ("expires_at", sa.DateTime(), None),
        ("source_updated_at", sa.DateTime(), None),
    ]
    for name, col_type, default in additions:
        if name not in columns:
            kwargs = {"nullable": True}
            if default is not None:
                kwargs["server_default"] = default
            op.add_column("job_listings", sa.Column(name, col_type, **kwargs))

    # Backfill source_key from the legacy source column before creating indexes.
    op.execute(sa.text("UPDATE job_listings SET source_key = source WHERE source_key = '' OR source_key IS NULL"))
    op.execute(sa.text(
        "UPDATE job_listings SET canonical_key = lower(trim(external_id)) "
        "WHERE (canonical_key = '' OR canonical_key IS NULL) AND external_id IS NOT NULL"
    ))

    existing_indexes = {idx["name"] for idx in sa.inspect(op.get_bind()).get_indexes("job_listings")}
    indexes = [
        ("ix_job_listings_canonical_key", ["canonical_key"]),
        ("ix_job_listings_source_key", ["source_key"]),
        ("ix_job_listings_normalized_location", ["normalized_location"]),
        ("ix_job_listings_employment_type", ["employment_type"]),
        ("ix_job_listings_remote_type", ["remote_type"]),
        ("ix_job_listings_experience_level", ["experience_level"]),
        ("ix_job_listings_posted_at", ["posted_at"]),
        ("ix_job_listings_expires_at", ["expires_at"]),
    ]
    for name, cols in indexes:
        if name not in existing_indexes:
            op.create_index(name, "job_listings", cols, unique=False)


def downgrade() -> None:
    op.drop_table("job_source_records")
    op.drop_table("job_sources")
    for name in [
        "ix_job_listings_expires_at",
        "ix_job_listings_posted_at",
        "ix_job_listings_experience_level",
        "ix_job_listings_remote_type",
        "ix_job_listings_employment_type",
        "ix_job_listings_normalized_location",
        "ix_job_listings_source_key",
        "ix_job_listings_canonical_key",
    ]:
        op.drop_index(name, table_name="job_listings")
    for name in [
        "source_updated_at",
        "expires_at",
        "posted_at",
        "salary_currency",
        "salary_max",
        "salary_min",
        "experience_level",
        "remote_type",
        "employment_type",
        "normalized_location",
        "source_key",
        "canonical_key",
    ]:
        op.drop_column("job_listings", name)
