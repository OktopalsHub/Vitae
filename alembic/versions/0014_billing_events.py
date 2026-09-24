"""Add idempotent billing event and subscription audit tables."""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "billing_events" not in tables:
        op.create_table(
            "billing_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("provider", sa.String(64), nullable=False, server_default="bachs"),
            sa.Column("event_id", sa.String(255), nullable=False),
            sa.Column("event_type", sa.String(128), nullable=False, server_default=""),
            sa.Column("payload_hash", sa.String(64), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("status", sa.String(32), nullable=False, server_default="received"),
            sa.Column("processed_at", sa.DateTime(), nullable=True),
            sa.Column("error", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("provider", "event_id", name="uq_billing_provider_event"),
        )
        op.create_index("ix_billing_events_provider", "billing_events", ["provider"])
        op.create_index("ix_billing_events_event_type", "billing_events", ["event_type"])
        op.create_index("ix_billing_events_status", "billing_events", ["status"])
        op.create_index("ix_billing_events_created_at", "billing_events", ["created_at"])
    if "billing_subscription_events" not in tables:
        op.create_table(
            "billing_subscription_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("profile_id", sa.Integer(), sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
            sa.Column("provider", sa.String(64), nullable=False, server_default="bachs"),
            sa.Column("subscription_id", sa.String(255), nullable=False, server_default=""),
            sa.Column("from_status", sa.String(64), nullable=False, server_default=""),
            sa.Column("to_status", sa.String(64), nullable=False, server_default=""),
            sa.Column("plan", sa.String(32), nullable=False, server_default=""),
            sa.Column("billing_event_id", sa.Integer(), sa.ForeignKey("billing_events.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        for name, cols in [
            ("ix_billing_sub_events_profile", ["profile_id"]),
            ("ix_billing_sub_events_subscription", ["subscription_id"]),
            ("ix_billing_sub_events_event", ["billing_event_id"]),
            ("ix_billing_sub_events_created_at", ["created_at"]),
        ]:
            op.create_index(name, "billing_subscription_events", cols)

def downgrade():
    op.drop_table("billing_subscription_events")
    op.drop_table("billing_events")
