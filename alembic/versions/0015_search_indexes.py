"""Add database indexes used by the job catalogue search path."""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {x["name"] for x in inspector.get_indexes("job_listings")}
    for name, cols in [
        ("ix_job_listings_active_posted", ["is_active", "posted_at"]),
        ("ix_job_listings_active_location", ["is_active", "normalized_location"]),
        ("ix_job_listings_active_remote_type", ["is_active", "remote_type"]),
        ("ix_job_listings_active_employment_type", ["is_active", "employment_type"]),
        ("ix_job_listings_active_experience", ["is_active", "experience_level"]),
    ]:
        if name not in existing:
            op.create_index(name, "job_listings", cols)

def downgrade():
    for name in [
        "ix_job_listings_active_experience",
        "ix_job_listings_active_employment_type",
        "ix_job_listings_active_remote_type",
        "ix_job_listings_active_location",
        "ix_job_listings_active_posted",
    ]:
        try:
            op.drop_index(name, table_name="job_listings")
        except Exception:
            pass
