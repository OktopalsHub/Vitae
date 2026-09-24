"""Add durable LLM request observability."""

from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "llm_requests" not in inspector.get_table_names():
        op.create_table(
            "llm_requests",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("provider", sa.String(64), nullable=False, index=True),
            sa.Column("model", sa.String(128), nullable=False, index=True),
            sa.Column("purpose", sa.String(64), nullable=False, index=True),
            sa.Column("prompt_version", sa.String(64), nullable=False, index=True),
            sa.Column("status", sa.String(32), nullable=False, index=True),
            sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("input_chars", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("output_chars", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("input_tokens", sa.Integer(), nullable=True),
            sa.Column("output_tokens", sa.Integer(), nullable=True),
            sa.Column("error_type", sa.String(128), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), index=True),
        )
    else:
        existing_indexes = {i["name"] for i in inspector.get_indexes("llm_requests")}
        for name, column in (
            ("ix_llm_requests_provider", "provider"),
            ("ix_llm_requests_model", "model"),
            ("ix_llm_requests_purpose", "purpose"),
            ("ix_llm_requests_prompt_version", "prompt_version"),
            ("ix_llm_requests_status", "status"),
            ("ix_llm_requests_created_at", "created_at"),
        ):
            if name not in existing_indexes:
                op.create_index(name, "llm_requests", [column])


def downgrade() -> None:
    op.drop_table("llm_requests")
