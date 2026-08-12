from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import get_settings, project_path
from app.db import get_db
from app.models import BillingPlan, User
from app.profile.loader import parse_cv_file
from app.services import rescore_user_jobs
from app.accounts import (
    apply_profile_from_user,
    ensure_account,
    get_active_profile,
    load_user_profile_dict,
    load_user_settings,
)
from app.accounts.paths import profile_data_dir
from app.accounts.llm_keys import (
    LLM_PROVIDERS,
    apply_llm_key_updates,
    has_llm_keys,
    providers_for_template,
)
from app.accounts.profile import get_profile_billing
from app.llm_providers import PLATFORM_PROVIDERS
from app.roles import is_admin
from app.web_helpers import (
    flash_redirect,
    read_upload_limited,
    require_profile_ready,
    template_ctx,
    validate_upload_content,
    validate_upload_filename,
)

router = APIRouter(tags=["settings"])
templates = Jinja2Templates(directory=str(project_path("app", "templates")))


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
):
    ensure_account(db, user)
    cfg = load_user_settings(db, user)
    profile = load_user_profile_dict(db, user)
    apply_profile = apply_profile_from_user(db, user)
    up = get_active_profile(db, user)
    billing = get_profile_billing(db, user, up)
    return templates.TemplateResponse(
        request,
        "settings.html",
        template_ctx(
            request,
            user,
            db,
            cfg=cfg,
            profile_name=profile.get("name") or (up.full_name if up else ""),
            profile_label=up.label if up else "Default",
            skill_count=len(profile.get("skills") or []),
            master_cv=(up.master_cv_path if up else "") or "",
            apply_profile=apply_profile,
            settings=get_settings(),
            has_llm_key=has_llm_keys(billing),
            llm_providers=providers_for_template(billing),
            platform_llm_providers=[
                {
                    "id": p.id,
                    "label": p.label,
                    "configured": bool(
                        (getattr(get_settings(), p.env_key_attr, "") or "").strip()
                    ),
                }
                for p in PLATFORM_PROVIDERS
            ],
            llm_provider=(billing.llm_provider if billing else "openai") or "openai",
            plan=(billing.plan if billing else BillingPlan.NONE.value),
            can_edit_llm_key=is_admin(user)
            or (billing and billing.plan == BillingPlan.BYOK_MONTHLY.value),
            staff_billing_exempt=is_admin(user),
        ),
    )


@router.post("/settings/reload-profile")
def reload_profile(
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
):
    ensure_account(db, user)
    up = get_active_profile(db, user)
    assert up
    path = Path(up.master_cv_path) if up.master_cv_path else None
    if not path or not path.exists():
        return flash_redirect("/settings", "Upload your CV first — there is no shared profile fallback.")
    try:
        parsed = parse_cv_file(path)
        up.profile_json = json.dumps(parsed)
        if parsed.get("name"):
            up.full_name = parsed["name"]
        up.profile_confirmed = False
        db.add(up)
        db.commit()
        return flash_redirect(
            "/onboarding/review/basics",
            "CV reloaded — please confirm your details again.",
        )
    except Exception as exc:  # noqa: BLE001
        return flash_redirect("/settings", f"Could not parse CV: {exc}")


@router.post("/settings/upload-cv")
async def upload_cv(
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
    file: UploadFile = File(...),
):
    ensure_account(db, user)
    try:
        name = validate_upload_filename(file.filename)
        content = read_upload_limited(await file.read())
        validate_upload_content(name, content)
    except ValueError as exc:
        return flash_redirect("/settings", str(exc))
    dest = profile_data_dir(user.id, get_active_profile(db, user).id) / name
    dest.write_bytes(content)
    up = get_active_profile(db, user)
    assert up
    up.master_cv_path = str(dest)
    try:
        parsed = parse_cv_file(dest)
        up.profile_json = json.dumps(parsed)
        if parsed.get("name"):
            up.full_name = parsed["name"]
        up.profile_confirmed = False
    except Exception as exc:  # noqa: BLE001
        return flash_redirect("/settings", f"Could not parse CV: {exc}")
    db.add(up)
    db.commit()
    return flash_redirect(
        "/onboarding/review/basics",
        "CV uploaded — review and confirm your sections.",
    )


@router.post("/settings/apply-profile")
def update_apply_profile(
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
    full_name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    linkedin: str = Form(""),
    github: str = Form(""),
    location_preference: str = Form(""),
    website: str = Form(""),
    note: str = Form(""),
    years_experience: str = Form(""),
    work_authorization: str = Form(""),
    salary_expectation: str = Form(""),
    earliest_start: str = Form(""),
):
    ensure_account(db, user)
    up = get_active_profile(db, user)
    assert up
    up.full_name = full_name
    up.email = email
    up.phone = phone
    up.linkedin = linkedin
    up.github = github
    up.location_preference = location_preference
    up.website = website
    up.note = note
    up.years_experience = years_experience
    up.work_authorization = work_authorization
    up.salary_expectation = salary_expectation
    up.earliest_start = earliest_start
    if full_name:
        sync_user = db.get(User, user.id)
        if sync_user:
            sync_user.full_name = full_name
            db.add(sync_user)
    db.add(up)
    db.commit()
    return flash_redirect("/settings", "Apply profile saved")


@router.post("/settings/llm-key")
async def save_llm_key(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
):
    ensure_account(db, user)
    billing = get_profile_billing(db, user)
    if not (is_admin(user) or billing.plan == BillingPlan.BYOK_MONTHLY.value):
        return flash_redirect("/settings", "LLM key is only used on the BYOK plan")

    form = await request.form()
    active = str(form.get("llm_provider") or "openai").strip().lower()
    new_keys = {name: str(form.get(f"{name}_api_key") or "") for name in LLM_PROVIDERS}
    clear = {
        name
        for name in LLM_PROVIDERS
        if str(form.get(f"clear_{name}") or "").lower() in {"1", "on", "true", "yes"}
    }
    apply_llm_key_updates(
        billing,
        active_provider=active,
        new_keys=new_keys,
        clear=clear,
    )
    db.add(billing)
    db.commit()
    return flash_redirect("/settings", "LLM providers saved")
