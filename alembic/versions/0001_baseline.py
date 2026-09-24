"""Create the current pre-normalization Vitae schema for new databases.

Existing databases are stamped at this revision after verification. New databases
can be created entirely through Alembic without application startup DDL.
"""

from alembic import op
from app.models import Base

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

_NORMALIZED = {
    "profile_experiences",
    "profile_education",
    "profile_skills",
    "profile_projects",
    "profile_certifications",
    "profile_preferences",
    "documents",
    "resume_versions",
}


def upgrade() -> None:
    bind = op.get_bind()
    for table in Base.metadata.sorted_tables:
        if table.name not in _NORMALIZED:
            table.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(Base.metadata.sorted_tables):
        if table.name not in _NORMALIZED:
            table.drop(bind=bind, checkfirst=True)
