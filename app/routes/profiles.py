"""Create / switch / rename / archive career profiles (each has its own subscription)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.accounts import ensure_account
from app.accounts.profile import (
    archive_profile,
    create_profile,
    get_active_profile,
    get_profile_billing,
    list_user_profiles,
    rename_profile,
    switch_active_profile,
)
from app.billing import PAID_PLANS
from app.config import project_path
from app.db import get_db
from app.models import User
from app.web_helpers import flash_redirect, require_user, template_ctx

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
    rows = []
    for p in profiles:
        billing = get_profile_billing(db, user, p)
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
            }
        )
    return templates.TemplateResponse(
        request,
        "profiles.html",
        template_ctx(request, user, db, profiles=rows, active_profile=active),
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
