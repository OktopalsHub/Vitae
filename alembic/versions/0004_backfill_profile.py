"""Backfill normalized profile rows from legacy profile_json fields."""

import json
from alembic import op
import sqlalchemy as sa

revision = "0004_backfill_profile"
down_revision = "0003_active_profile_fk"
branch_labels = None
depends_on = None


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _rows(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [value] if value.strip() else []
    return []


def upgrade() -> None:
    conn = op.get_bind()
    profiles = conn.execute(
        sa.text("SELECT id, profile_json FROM profiles")
    ).mappings().all()

    for profile in profiles:
        try:
            data = json.loads(profile["profile_json"] or "{}")
        except (TypeError, ValueError):
            continue

        pid = profile["id"]

        skills = data.get("skills") or []
        for index, skill in enumerate(skills):
            if isinstance(skill, dict):
                name = _text(skill.get("name") or skill.get("skill"))
                category = _text(skill.get("category"))
                proficiency = _text(skill.get("proficiency"))
            else:
                name = _text(skill)
                category = proficiency = ""
            if name:
                conn.execute(
                    sa.text(
                        "INSERT INTO profile_skills "
                        "(profile_id, name, category, proficiency, sort_order) "
                        "SELECT :pid, :name, :category, :proficiency, :sort_order "
                        "WHERE NOT EXISTS "
                        "(SELECT 1 FROM profile_skills WHERE profile_id=:pid AND name=:name)"
                    ),
                    {"pid": pid, "name": name[:255], "category": category[:64],
                     "proficiency": proficiency[:64], "sort_order": index},
                )

        for index, item in enumerate(_rows(data.get("experience_raw"))):
            if isinstance(item, dict):
                position = _text(item.get("position") or item.get("title"))
                company = _text(item.get("company") or item.get("employer"))
                location = _text(item.get("location"))
                description = _text(item.get("description") or item.get("summary"))
                start_date = _text(item.get("start_date") or item.get("start"))
                end_date = _text(item.get("end_date") or item.get("end"))
            else:
                position = company = location = start_date = end_date = ""
                description = _text(item)
            if description or position or company:
                conn.execute(
                    sa.text(
                        "INSERT INTO profile_experiences "
                        "(profile_id, position, company, location, start_date, end_date, description, sort_order) "
                        "VALUES (:pid,:position,:company,:location,:start_date,:end_date,:description,:sort_order)"
                    ),
                    {"pid": pid, "position": position[:255], "company": company[:255],
                     "location": location[:255], "start_date": start_date[:64],
                     "end_date": end_date[:64], "description": description,
                     "sort_order": index},
                )

        for index, item in enumerate(_rows(data.get("education_raw"))):
            if isinstance(item, dict):
                institution = _text(item.get("institution") or item.get("school"))
                degree = _text(item.get("degree"))
                field = _text(item.get("field_of_study") or item.get("field"))
                description = _text(item.get("description"))
                start_date = _text(item.get("start_date") or item.get("start"))
                end_date = _text(item.get("end_date") or item.get("end"))
            else:
                institution = degree = field = start_date = end_date = ""
                description = _text(item)
            if description or institution or degree:
                conn.execute(
                    sa.text(
                        "INSERT INTO profile_education "
                        "(profile_id, institution, degree, field_of_study, start_date, end_date, description, sort_order) "
                        "VALUES (:pid,:institution,:degree,:field,:start_date,:end_date,:description,:sort_order)"
                    ),
                    {"pid": pid, "institution": institution[:255], "degree": degree[:255],
                     "field": field[:255], "start_date": start_date[:64],
                     "end_date": end_date[:64], "description": description,
                     "sort_order": index},
                )

        for index, item in enumerate(_rows(data.get("projects_raw"))):
            if isinstance(item, dict):
                name = _text(item.get("name") or item.get("title"))
                description = _text(item.get("description") or item.get("summary"))
                url = _text(item.get("url") or item.get("link"))
                technologies = item.get("technologies") or item.get("tech") or []
                if not isinstance(technologies, list):
                    technologies = [technologies]
            else:
                name, url, technologies = "", "", []
                description = _text(item)
            if name or description:
                conn.execute(
                    sa.text(
                        "INSERT INTO profile_projects "
                        "(profile_id, name, description, url, technologies, sort_order) "
                        "VALUES (:pid,:name,:description,:url,:technologies,:sort_order)"
                    ),
                    {"pid": pid, "name": name[:255], "description": description,
                     "url": url[:1024], "technologies": json.dumps(technologies),
                     "sort_order": index},
                )

        conn.execute(
            sa.text(
                "INSERT INTO profile_preferences "
                "(profile_id, preferred_locations, remote_only, employment_types, target_titles, "
                "min_salary, currency, work_authorization) "
                "SELECT :pid, '[]', false, '[]', '[]', NULL, '', :authorization "
                "WHERE NOT EXISTS (SELECT 1 FROM profile_preferences WHERE profile_id=:pid)"
            ),
            {"pid": pid, "authorization": ""},
        )


def downgrade() -> None:
    pass
