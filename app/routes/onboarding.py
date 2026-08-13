from __future__ import annotations

import json

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import project_path
from app.db import get_db
from app.models import User
from app.profile.loader import contact_parts_from_profile, parse_cv_file
from app.services import rescore_user_jobs_background
from app.accounts import (
    ensure_account,
    get_active_profile,
    load_user_profile_dict,
)
from app.accounts.paths import profile_data_dir
from app.web_helpers import (
    flash_redirect,
    read_upload_limited,
    require_user,
    template_ctx,
    validate_upload_content,
    validate_upload_filename,
)

router = APIRouter(tags=["onboarding"])
templates = Jinja2Templates(directory=str(project_path("app", "templates")))

ONBOARDING_SECTIONS = [
    ("basics", "Basics"),
    ("summary", "Summary"),
    ("skills", "Skills"),
    ("experience", "Work experience"),
    ("projects", "Projects"),
    ("education", "Education"),
    ("confirm", "Confirm"),
]


def _next_section(current: str) -> str:
    keys = [k for k, _ in ONBOARDING_SECTIONS]
    if current not in keys:
        return keys[0]
    idx = keys.index(current)
    return keys[min(idx + 1, len(keys) - 1)]


def _prev_section(current: str) -> str:
    keys = [k for k, _ in ONBOARDING_SECTIONS]
    if current not in keys:
        return keys[0]
    idx = keys.index(current)
    return keys[max(idx - 1, 0)]


@router.get("/onboarding", response_class=HTMLResponse)
def onboarding_upload(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    ensure_account(db, user)
    up = get_active_profile(db, user)
    assert up
    if up.profile_confirmed:
        return RedirectResponse("/jobs", status_code=303)
    if up.master_cv_path and up.profile_json and up.profile_json != "{}":
        return RedirectResponse("/onboarding/review/basics", status_code=303)
    return templates.TemplateResponse(
        request,
        "onboarding/upload.html",
        template_ctx(
            request,
            user,
            db,
            error=request.query_params.get("error", ""),
        ),
    )


@router.post("/onboarding")
async def onboarding_upload_post(
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    file: UploadFile = File(...),
):
    ensure_account(db, user)
    try:
        name = validate_upload_filename(file.filename)
        content = read_upload_limited(await file.read())
        validate_upload_content(name, content)
    except ValueError as exc:
        return flash_redirect("/onboarding", str(exc))

    dest = profile_data_dir(user.id, get_active_profile(db, user).id) / name
    dest.write_bytes(content)
    try:
        parsed = parse_cv_file(dest)
    except Exception as exc:  # noqa: BLE001
        return flash_redirect("/onboarding", f"Could not parse CV: {exc}")

    up = get_active_profile(db, user)
    assert up
    up.master_cv_path = str(dest)
    up.profile_json = json.dumps(parsed)
    up.profile_confirmed = False
    chips = contact_parts_from_profile(parsed)
    if chips.get("full_name"):
        up.full_name = chips["full_name"]
        sync_user = db.get(User, user.id)
        if sync_user:
            sync_user.full_name = chips["full_name"]
            db.add(sync_user)
    if chips.get("email"):
        up.email = chips["email"]
    if chips.get("phone"):
        up.phone = chips["phone"]
    if chips.get("linkedin"):
        up.linkedin = chips["linkedin"]
    if chips.get("github"):
        up.github = chips["github"]
    if chips.get("website"):
        up.website = chips["website"]
    db.add(up)
    db.commit()
    return RedirectResponse("/onboarding/review/basics", status_code=303)


@router.get("/onboarding/review/{section}", response_class=HTMLResponse)
def onboarding_review(
    section: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    ensure_account(db, user)
    up = get_active_profile(db, user)
    assert up
    if up.profile_confirmed:
        return RedirectResponse("/jobs", status_code=303)
    if not up.master_cv_path:
        return RedirectResponse("/onboarding", status_code=303)
    keys = {k for k, _ in ONBOARDING_SECTIONS}
    if section not in keys:
        return RedirectResponse("/onboarding/review/basics", status_code=303)

    profile = load_user_profile_dict(db, user)
    return templates.TemplateResponse(
        request,
        "onboarding/review.html",
        template_ctx(
            request,
            user,
            db,
            section=section,
            sections=ONBOARDING_SECTIONS,
            profile=profile,
            apply= {
                "full_name": up.full_name,
                "email": up.email,
                "phone": up.phone,
                "linkedin": up.linkedin,
                "github": up.github,
                "website": up.website,
                "location_preference": up.location_preference,
                "years_experience": up.years_experience,
            },
            next_section=_next_section(section),
            prev_section=_prev_section(section),
        ),
    )


@router.post("/onboarding/review/{section}")
async def onboarding_review_save(
    section: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    # basics
    full_name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    linkedin: str = Form(""),
    github: str = Form(""),
    website: str = Form(""),
    location_preference: str = Form(""),
    years_experience: str = Form(""),
    # text sections
    summary: str = Form(""),
    skills: str = Form(""),
    experience: str = Form(""),
    projects: str = Form(""),
    education: str = Form(""),
    action: str = Form("next"),
):
    ensure_account(db, user)
    up = get_active_profile(db, user)
    assert up
    try:
        profile = json.loads(up.profile_json or "{}")
    except json.JSONDecodeError:
        profile = {}
    if not isinstance(profile, dict):
        profile = {}

    if section == "basics":
        up.full_name = full_name.strip()
        up.email = email.strip()
        up.phone = phone.strip()
        up.linkedin = linkedin.strip()
        up.github = github.strip()
        up.website = website.strip()
        up.location_preference = location_preference.strip()
        up.years_experience = years_experience.strip() or up.years_experience
        profile["name"] = up.full_name
        if full_name.strip():
            sync_user = db.get(User, user.id)
            if sync_user:
                sync_user.full_name = full_name.strip()
                db.add(sync_user)
    elif section == "summary":
        profile["summary"] = summary.strip()
    elif section == "skills":
        skills_list = [s.strip() for s in skills.replace("\n", ",").split(",") if s.strip()]
        profile["skills"] = skills_list
    elif section == "experience":
        lines = [ln.strip() for ln in experience.splitlines() if ln.strip()]
        profile["experience_raw"] = lines
    elif section == "projects":
        lines = [ln.strip() for ln in projects.splitlines() if ln.strip()]
        profile["projects_raw"] = lines
    elif section == "education":
        lines = [ln.strip() for ln in education.splitlines() if ln.strip()]
        profile["education_raw"] = lines
    elif section == "confirm":
        up.profile_json = json.dumps(profile)
        up.profile_confirmed = True
        db.add(up)
        db.commit()
        # Full catalogue rescore is too slow for the request (Neon round-trips).
        background_tasks.add_task(rescore_user_jobs_background, user.id)
        return flash_redirect(
            "/billing",
            "Profile saved. Choose an AI plan, or skip to continue to jobs.",
        )

    up.profile_json = json.dumps(profile)
    db.add(up)
    db.commit()

    if action == "back":
        return RedirectResponse(f"/onboarding/review/{_prev_section(section)}", status_code=303)
    if section == "education" or _next_section(section) == "confirm":
        return RedirectResponse("/onboarding/review/confirm", status_code=303)
    return RedirectResponse(f"/onboarding/review/{_next_section(section)}", status_code=303)
