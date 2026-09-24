"""Create durable application lifecycle and history tables."""

from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "applications" not in tables:
        op.create_table(
            "applications",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("profile_id", sa.Integer(), sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
            sa.Column("listing_id", sa.Integer(), sa.ForeignKey("job_listings.id", ondelete="SET NULL"), nullable=True),
            sa.Column("resume_artifact_id", sa.Integer(), sa.ForeignKey("resume_artifacts.id", ondelete="SET NULL"), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
            sa.Column("channel", sa.String(32), nullable=False, server_default="manual"),
            sa.Column("external_url", sa.String(1024), nullable=False, server_default=""),
            sa.Column("external_application_id", sa.String(255), nullable=False, server_default=""),
            sa.Column("cover_blurb", sa.Text(), nullable=False, server_default=""),
            sa.Column("answers_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("notes", sa.Text(), nullable=False, server_default=""),
            sa.Column("applied_at", sa.DateTime(), nullable=True),
            sa.Column("last_status_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("profile_id", "listing_id", name="uq_application_profile_listing"),
        )
        for name, columns in (
            ("ix_applications_user_id", ["user_id"]),
            ("ix_applications_profile_id", ["profile_id"]),
            ("ix_applications_listing_id", ["listing_id"]),
            ("ix_applications_resume_artifact_id", ["resume_artifact_id"]),
            ("ix_applications_status", ["status"]),
            ("ix_applications_applied_at", ["applied_at"]),
            ("ix_applications_last_status_at", ["last_status_at"]),
        ):
            op.create_index(name, "applications", columns)

    if "application_events" not in tables:
        op.create_table(
            "application_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
            sa.Column("from_status", sa.String(32), nullable=True),
            sa.Column("to_status", sa.String(32), nullable=False),
            sa.Column("note", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        for name, columns in (
            ("ix_application_events_application_id", ["application_id"]),
            ("ix_application_events_to_status", ["to_status"]),
            ("ix_application_events_created_at", ["created_at"]),
        ):
            op.create_index(name, "application_events", columns)


def downgrade() -> None:
    op.drop_table("application_events")
    op.drop_table("applications")
