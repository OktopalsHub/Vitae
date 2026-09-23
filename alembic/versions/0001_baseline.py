"""Baseline for existing Vitae databases.

Existing databases are stamped to this revision after verification. No schema
mutation occurs during application startup.
"""
revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    pass

def downgrade() -> None:
    pass
