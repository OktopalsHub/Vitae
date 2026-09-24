"""Add durable auto-apply run and item tracking."""

from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "auto_apply_runs" not in tables:
        op.create_table(
            "auto_apply_runs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("profile_id", sa.Integer(), sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
            sa.Column("max_applications", sa.Integer(), nullable=False, server_default="10"),
            sa.Column("prepared_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("submitted_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("requires_review", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        for name, cols in (
            ("ix_auto_apply_runs_user_id", ["user_id"]),
            ("ix_auto_apply_runs_profile_id", ["profile_id"]),
            ("ix_auto_apply_runs_status", ["status"]),
            ("ix_auto_apply_runs_created_at", ["created_at"]),
        ):
            op.create_index(name, "auto_apply_runs", cols)

    if "auto_apply_items" not in tables:
        op.create_table(
            "auto_apply_items",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("run_id", sa.Integer(), sa.ForeignKey("auto_apply_runs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id", ondelete="SET NULL"), nullable=True),
            sa.Column("listing_id", sa.Integer(), sa.ForeignKey("job_listings.id", ondelete="CASCADE"), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("requires_review", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("review_reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("run_id", "listing_id", name="uq_auto_apply_run_listing"),
        )
        for name, cols in (
            ("ix_auto_apply_items_run_id", ["run_id"]),
            ("ix_auto_apply_items_application_id", ["application_id"]),
            ("ix_auto_apply_items_listing_id", ["listing_id"]),
            ("ix_auto_apply_items_status", ["status"]),
        ):
            op.create_index(name, "auto_apply_items", cols)


def downgrade() -> None:
    op.drop_table("auto_apply_items")
    op.drop_table("auto_apply_runs")
