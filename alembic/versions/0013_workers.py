"""Add durable background worker jobs."""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    if "worker_jobs" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "worker_jobs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("queue", sa.String(64), nullable=False, server_default="default"),
            sa.Column("kind", sa.String(128), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("payload_hash", sa.String(64), nullable=False, server_default=""),
            sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
            sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("idempotency_key", sa.String(255), nullable=True, unique=True),
            sa.Column("available_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("locked_at", sa.DateTime(), nullable=True),
            sa.Column("last_heartbeat_at", sa.DateTime(), nullable=True),
            sa.Column("lock_owner", sa.String(255), nullable=False, server_default=""),
            sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
            sa.Column("failed_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        for name, cols in [
            ("ix_worker_jobs_queue", ["queue"]), ("ix_worker_jobs_kind", ["kind"]),
            ("ix_worker_jobs_status", ["status"]), ("ix_worker_jobs_available_at", ["available_at"]),
            ("ix_worker_jobs_created_at", ["created_at"]),
        ]:
            op.create_index(name, "worker_jobs", cols)

def downgrade():
    op.drop_table("worker_jobs")
