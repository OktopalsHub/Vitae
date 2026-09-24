"""Create the current pre-normalization Vitae schema for new databases.

Existing databases are stamped at this revision after verification. New databases
can be created entirely through Alembic without application startup DDL.

On PostgreSQL, CREATE TABLE validates foreign key targets immediately, so the
inline-FK approach that SQLite tolerates cannot work here: the profiles <-> users
cycle has no valid creation order, and this revision creates tables that reference
normalized tables only created by 0002. Tables are therefore created without FK
constraints and the constraints whose targets already exist are added afterwards
via ALTER TABLE.
"""

from alembic import op
from sqlalchemy import inspect as sa_inspect
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


def fk_name(table_name: str, columns: list[str]) -> str:
    return f"fk_{table_name}_{'_'.join(columns)}"[:63]


def add_fk(table, fk) -> None:
    local_cols = list(fk.columns.keys())
    remote_cols = [el.column.name for el in fk.elements]
    op.create_foreign_key(
        fk_name(table.name, local_cols),
        table.name,
        fk.elements[0].column.table.name,
        local_cols,
        remote_cols,
        ondelete=fk.ondelete,
        onupdate=fk.onupdate,
    )


def upgrade() -> None:
    bind = op.get_bind()
    tables = [t for t in Base.metadata.sorted_tables if t.name not in _NORMALIZED]
    created = {t.name for t in tables}

    if bind.dialect.name == "sqlite":
        # SQLite does not validate FK targets at CREATE TABLE time and cannot
        # ALTER in constraints, so keep them inline (cycle and deferred targets
        # are both safe here).
        for table in tables:
            table.create(bind, checkfirst=True)
        return

    detached: list = []
    try:
        for table in tables:
            for fk in list(table.foreign_key_constraints):
                table.constraints.discard(fk)
                table.foreign_key_constraints.discard(fk)
                detached.append((table, fk))
            table.create(bind, checkfirst=True)
    finally:
        for table, fk in detached:
            table.constraints.add(fk)
            table.foreign_key_constraints.add(fk)

    for table, fk in detached:
        # Targets created by later revisions (the _NORMALIZED set) are attached
        # by 0002 once those tables exist.
        if fk.elements[0].column.table.name in created:
            add_fk(table, fk)


def downgrade() -> None:
    bind = op.get_bind()
    tables = [t for t in Base.metadata.sorted_tables if t.name not in _NORMALIZED]

    if bind.dialect.name != "sqlite":
        inspector = sa_inspect(bind)
        for table in tables:
            if not inspector.has_table(table.name):
                continue
            for fk in inspector.get_foreign_keys(table.name):
                if fk.get("name"):
                    op.drop_constraint(
                        fk["name"], table.name, type_="foreignkey"
                    )

    for table in reversed(tables):
        table.drop(bind, checkfirst=True)
