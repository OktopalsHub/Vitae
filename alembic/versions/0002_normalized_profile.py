"""Create normalized profile and document tables."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision = "0002_normalized_profile"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "profile_experiences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.String(255), nullable=False, server_default=""),
        sa.Column("company", sa.String(255), nullable=False, server_default=""),
        sa.Column("location", sa.String(255), nullable=False, server_default=""),
        sa.Column("start_date", sa.String(64), nullable=False, server_default=""),
        sa.Column("end_date", sa.String(64), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_profile_experiences_profile_id", "profile_experiences", ["profile_id"])

    op.create_table(
        "profile_education",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("institution", sa.String(255), nullable=False, server_default=""),
        sa.Column("degree", sa.String(255), nullable=False, server_default=""),
        sa.Column("field_of_study", sa.String(255), nullable=False, server_default=""),
        sa.Column("start_date", sa.String(64), nullable=False, server_default=""),
        sa.Column("end_date", sa.String(64), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_profile_education_profile_id", "profile_education", ["profile_id"])

    op.create_table(
        "profile_skills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(64), nullable=False, server_default=""),
        sa.Column("proficiency", sa.String(64), nullable=False, server_default=""),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("profile_id", "name", name="uq_profile_skill"),
    )
    op.create_index("ix_profile_skills_profile_id", "profile_skills", ["profile_id"])

    op.create_table(
        "profile_projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("url", sa.String(1024), nullable=False, server_default=""),
        sa.Column("technologies", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_profile_projects_profile_id", "profile_projects", ["profile_id"])

    op.create_table(
        "profile_certifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False, server_default=""),
        sa.Column("issuer", sa.String(255), nullable=False, server_default=""),
        sa.Column("issue_date", sa.String(64), nullable=False, server_default=""),
        sa.Column("expiry_date", sa.String(64), nullable=False, server_default=""),
        sa.Column("credential_url", sa.String(1024), nullable=False, server_default=""),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_profile_certifications_profile_id", "profile_certifications", ["profile_id"])

    op.create_table(
        "profile_preferences",
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("preferred_locations", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("remote_only", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("employment_types", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("target_titles", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("min_salary", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(16), nullable=False, server_default=""),
        sa.Column("work_authorization", sa.String(512), nullable=False, server_default=""),
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False, unique=True),
        sa.Column("original_filename", sa.String(512), nullable=False, server_default=""),
        sa.Column("content_type", sa.String(255), nullable=False, server_default=""),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checksum", sa.String(128), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
    )
    op.create_index("ix_documents_user_id", "documents", ["user_id"])
    op.create_index("ix_documents_profile_id", "documents", ["profile_id"])

    op.create_table(
        "resume_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("extraction_version", sa.String(64), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.UniqueConstraint("profile_id", "version", name="uq_resume_version"),
    )
    op.create_index("ix_resume_versions_profile_id", "resume_versions", ["profile_id"])

    # resume_generations (created by 0001) references resume_versions, which did
    # not exist yet on dialects that validate FK targets at CREATE TABLE time.
    # SQLite carries the FK inline from 0001, so only add it when missing.
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if inspector.has_table("resume_generations"):
        existing = inspector.get_foreign_keys("resume_generations")
        if not any(fk.get("referred_table") == "resume_versions" for fk in existing):
            op.create_foreign_key(
                "fk_resume_generations_source_resume_version_id",
                "resume_generations",
                "resume_versions",
                ["source_resume_version_id"],
                ["id"],
                ondelete="set null",
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if inspector.has_table("resume_generations"):
        for fk in inspector.get_foreign_keys("resume_generations"):
            if fk.get("referred_table") == "resume_versions" and fk.get("name"):
                op.drop_constraint(
                    fk["name"], "resume_generations", type_="foreignkey"
                )
    op.drop_table("resume_versions")
    op.drop_table("documents")
    op.drop_table("profile_preferences")
    op.drop_table("profile_certifications")
    op.drop_table("profile_projects")
    op.drop_table("profile_skills")
    op.drop_table("profile_education")
    op.drop_table("profile_experiences")
