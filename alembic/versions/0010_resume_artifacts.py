"""Track immutable resume generations and generated artifacts."""

from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "resume_generations" not in tables:
        op.create_table(
            "resume_generations",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("profile_id", sa.Integer(), sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
            sa.Column("listing_id", sa.Integer(), sa.ForeignKey("job_listings.id", ondelete="CASCADE"), nullable=False),
            sa.Column("source_resume_version_id", sa.Integer(), sa.ForeignKey("resume_versions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("generator_version", sa.String(64), nullable=False, server_default="resume.v1"),
            sa.Column("prompt_version", sa.String(64), nullable=False, server_default="cv_tailor.v2"),
            sa.Column("status", sa.String(32), nullable=False, server_default="completed"),
            sa.Column("used_fallback", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("output_dir", sa.String(1024), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        for name, column in (
            ("ix_resume_generations_user_id", "user_id"),
            ("ix_resume_generations_profile_id", "profile_id"),
            ("ix_resume_generations_listing_id", "listing_id"),
            ("ix_resume_generations_source_resume_version_id", "source_resume_version_id"),
            ("ix_resume_generations_status", "status"),
            ("ix_resume_generations_created_at", "created_at"),
        ):
            op.create_index(name, "resume_generations", [column])

    if "resume_artifacts" not in tables:
        op.create_table(
            "resume_artifacts",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("generation_id", sa.Integer(), sa.ForeignKey("resume_generations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("format", sa.String(16), nullable=False),
            sa.Column("storage_key", sa.String(1024), nullable=False, unique=True),
            sa.Column("filename", sa.String(512), nullable=False),
            sa.Column("content_type", sa.String(255), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("checksum", sa.String(128), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("generation_id", "format", name="uq_resume_artifact_format"),
        )
        for name, column in (
            ("ix_resume_artifacts_generation_id", "generation_id"),
            ("ix_resume_artifacts_created_at", "created_at"),
        ):
            op.create_index(name, "resume_artifacts", [column])


def downgrade() -> None:
    op.drop_table("resume_artifacts")
    op.drop_table("resume_generations")
