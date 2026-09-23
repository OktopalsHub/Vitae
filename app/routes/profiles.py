"""Create / switch / rename / archive career profiles (each has its own subscription)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.accounts import ensure_account
from app.accounts.profile import (
    _owned_alive_profile,
    archive_profile,
    create_profile,
    get_active_profile,
    list_user_profiles,
    rename_profile,
    switch_active_profile,
)
from app.accounts.bootstrap import ensure_profile_billing
from app.billing import PAID_PLANS
from app.profile.cv import list_resume_versions, register_cv_version
from app.profile.loader import contact_parts_from_profile, parse_cv_file
from app.config import project_path
from app.db import get_db
from app.models import ProfileBilling, User
from app.web_helpers import (
    flash_redirect,
    read_upload_limited,
    require_user,
    resolve_original_cv,
    template_ctx,
    validate_upload_content,
    validate_upload_filename,
)

router = APIRouter(tags=["profiles"])
templates = Jinja2Templates(directory=str(project_path("app", "templates")))

_ACTIVE_SUB = frozenset({"active", "trialing"})


@router.get("/profiles", response_class=HTMLResponse)
def profiles_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    ensure_account(db, user)
    active = get_active_profile(db, user)
    profiles = list_user_profiles(db, user)

    # Batch-load all billing records to avoid N+1 queries.
    profile_ids = [p.id for p in profiles]
    billing_rows = (
        db.query(ProfileBilling)
        .filter(ProfileBilling.profile_id.in_(profile_ids))
        .all()
        if profile_ids
        else []
    )
    billing_map = {b.profile_id: b for b in billing_rows}

    rows = []
    for p in profiles:
        billing = billing_map.get(p.id)
        if billing is None:
            billing = ensure_profile_billing(db, user, p)
        status = (billing.subscription_status or "").lower()
        paid_active = billing.plan in PAID_PLANS and status in _ACTIVE_SUB
        rows.append(
            {
                "id": p.id,
                "label": p.label,
                "confirmed": bool(p.profile_confirmed),
                "plan": billing.plan,
                "subscription_status": billing.subscription_status,
                "active": p.id == active.id,
                "can_archive": len(profiles) > 1 and not paid_active,
                "paid_active": paid_active,
                "has_cv": bool(p.master_cv_path),
            }
        )
    resume_versions = list_resume_versions(db, user, active.id)
    return templates.TemplateResponse(
        request,
        "profiles.html",
        template_ctx(
            request,
            user,
            db,
            profiles=rows,
            active_profile=active,
            resume_versions=resume_versions,
        ),
    )


@router.get("/profiles/{profile_id}/download-cv")
def download_profile_cv(
    profile_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    """Serve the originally uploaded master CV for one of the user's profiles."""
    try:
        profile = _owned_alive_profile(db, user, profile_id)
    except ValueError:
        raise HTTPException(404, "No CV on file") from None
    try:
        path, media = resolve_original_cv(user.id, profile.master_cv_path)
    except PermissionError:
        raise HTTPException(404, "No CV on file") from None
    return FileResponse(path, media_type=media, filename=path.name)



@router.post("/profiles/{profile_id}/replace-cv")
async def replace_profile_cv(
    profile_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    ensure_account(db, user)
    try:
        profile = _owned_alive_profile(db, user, profile_id)
        name = validate_upload_filename(file.filename)
        content = read_upload_limited(await file.read())
        validate_upload_content(name, content)
    except (ValueError, PermissionError) as exc:
        return flash_redirect("/profiles", str(exc))

    dest = profile_data_dir(user.id, profile.id) / name
    dest.write_bytes(content)
    try:
        parsed = parse_cv_file(dest)
    except Exception as exc:
        return flash_redirect("/profiles", f"Could not parse CV: {exc}")

    chips = contact_parts_from_profile(parsed)
    if chips.get("full_name"):
        profile.full_name = chips["full_name"]
    if chips.get("email"):
        profile.email = chips["email"]
    if chips.get("phone"):
        profile.phone = chips["phone"]
    if chips.get("linkedin"):
        profile.linkedin = chips["linkedin"]
    if chips.get("github"):
        profile.github = chips["github"]
    if chips.get("website"):
        profile.website = chips["website"]
    profile.profile_json = json.dumps(parsed)
    profile.profile_confirmed = False

    content_type = (
        "application/pdf"
        if name.lower().endswith(".pdf")
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    register_cv_version(
        db,
        user,
        profile,
        dest,
        parsed,
        content_type=content_type,
    )
    db.commit()
    return flash_redirect(
        f"/onboarding/review/basics",
        f"CV replaced. Version {list_resume_versions(db, user, profile.id)[0].version} is ready for review.",
    )


@router.post("/profiles/create")
def profiles_create(
    label: str = Form("New track"),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    ensure_account(db, user)
    profile = create_profile(db, user, label, switch_to=True)
    db.commit()
    if profile.profile_confirmed:
        return flash_redirect("/jobs", f"Switched to “{profile.label}”.")
    return flash_redirect(
        "/onboarding",
        f"Created “{profile.label}” — upload a CV and confirm this track. Billing is separate per profile.",
    )


@router.post("/profiles/switch")
def profiles_switch(
    profile_id: int = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    ensure_account(db, user)
    try:
        profile = switch_active_profile(db, user, profile_id)
    except ValueError as exc:
        return flash_redirect("/profiles", str(exc))
    db.commit()
    if not profile.profile_confirmed:
        return flash_redirect(
            "/onboarding",
            f"Switched to “{profile.label}” — finish onboarding for this track.",
        )
    return flash_redirect("/jobs", f"Switched to “{profile.label}”.")


@router.post("/profiles/rename")
def profiles_rename(
    profile_id: int = Form(...),
    label: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    ensure_account(db, user)
    try:
        profile = rename_profile(db, user, profile_id, label)
    except ValueError as exc:
        return flash_redirect("/profiles", str(exc))
    db.commit()
    return flash_redirect("/profiles", f"Renamed to “{profile.label}”.")


@router.post("/profiles/archive")
def profiles_archive(
    profile_id: int = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    ensure_account(db, user)
    try:
        profile = archive_profile(db, user, profile_id)
    except ValueError as exc:
        return flash_redirect("/profiles", str(exc))
    db.commit()
    return flash_redirect("/profiles", f"Archived “{profile.label}”.")
