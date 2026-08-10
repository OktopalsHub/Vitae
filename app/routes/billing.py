from __future__ import annotations

import json
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.accounts import detect_billing_region_detail, ensure_account, FREE_CLEAR_MATCHES
from app.accounts.profile import get_active_profile, get_profile_billing
from app.billing import PAID_PLANS, create_checkout_session, plan_from_metadata, verify_bachs_webhook
from app.config import project_path
from app.db import get_db
from app.models import BillingPlan, Profile, ProfileBilling, User
from app.roles import is_admin
from app.web_helpers import flash_redirect, require_user, template_ctx

router = APIRouter(tags=["billing"])
templates = Jinja2Templates(directory=str(project_path("app", "templates")))


def _sync_region_from_request(request: Request, billing: ProfileBilling, db: Session) -> tuple[str, str]:
    """Always price from live geo (headers / locale cookie) — never a user-selected region."""
    region, source = detect_billing_region_detail(dict(request.headers), dict(request.cookies))
    if billing.billing_region != region:
        billing.billing_region = region
        db.add(billing)
        db.commit()
    return region, source


@router.get("/billing", response_class=HTMLResponse)
def billing_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    ensure_account(db, user)
    profile = get_active_profile(db, user)
    billing = get_profile_billing(db, user, profile)
    region, region_source = _sync_region_from_request(request, billing, db)
    is_ng = region == "ng"
    return templates.TemplateResponse(
        "billing.html",
        template_ctx(
            request,
            user,
            db,
            region=region,
            region_source=region_source,
            plan=billing.plan,
            subscription_status=billing.subscription_status,
            price_byok="₦2,000" if is_ng else "$5",
            price_platform="₦5,000" if is_ng else "$10",
            free_clear=FREE_CLEAR_MATCHES,
            billing_profile=profile,
        ),
    )


@router.post("/billing/checkout")
async def billing_checkout(
    request: Request,
    plan: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    if plan not in PAID_PLANS:
        raise HTTPException(400, "Invalid plan")
    if is_admin(user):
        return flash_redirect("/billing", "Staff accounts do not need a paid plan.")
    ensure_account(db, user)
    profile = get_active_profile(db, user)
    billing = get_profile_billing(db, user, profile)
    region, _ = _sync_region_from_request(request, billing, db)
    base = str(request.base_url).rstrip("/")
    try:
        session = await create_checkout_session(
            email=user.email,
            name=user.full_name or user.email,
            plan=plan,
            region=region,
            success_url=f"{base}/billing?flash={quote('Payment received — confirming…')}",
            cancel_url=f"{base}/billing?flash={quote('Checkout canceled')}",
            metadata={
                "user_id": str(user.id),
                "profile_id": str(profile.id),
                "plan": plan,
                "region": region,
            },
        )
    except Exception as exc:  # noqa: BLE001
        return flash_redirect("/billing", f"Checkout failed: {exc}")

    data = session.get("data") if isinstance(session.get("data"), dict) else session
    checkout_id = data.get("checkout_id") or data.get("id") or ""
    url = data.get("url") or data.get("checkout_url") or data.get("link") or ""
    if checkout_id:
        billing.last_checkout_id = str(checkout_id)
        db.add(billing)
        db.commit()
    if not url:
        return flash_redirect(
            "/billing",
            "Checkout created but no redirect URL returned. Try again in a moment.",
        )
    return RedirectResponse(url=url, status_code=303)


def _apply_paid_plan(billing: ProfileBilling, plan: str, status: str = "active") -> None:
    if plan not in PAID_PLANS:
        return
    billing.plan = plan
    billing.subscription_status = status


def _resolve_billing_for_webhook(
    db: Session, user: User, meta: dict
) -> ProfileBilling:
    ensure_account(db, user)
    profile_id_raw = str(meta.get("profile_id") or "").strip()
    profile: Profile | None = None
    if profile_id_raw.isdigit():
        candidate = db.get(Profile, int(profile_id_raw))
        if candidate is not None and candidate.user_id == user.id:
            profile = candidate
    if profile is None:
        profile = get_active_profile(db, user)
    return get_profile_billing(db, user, profile)


@router.post("/webhooks/bachs")
async def bachs_webhook(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    sig = request.headers.get("X-Bachs-Signature", "")
    ts = request.headers.get("X-Bachs-Timestamp", "")
    if not verify_bachs_webhook(raw, sig, ts):
        raise HTTPException(400, "Invalid signature")
    try:
        event = json.loads(raw.decode() or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Invalid JSON") from exc

    etype = event.get("type") or ""
    data = event.get("data") or {}
    meta = data.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except json.JSONDecodeError:
            meta = {}

    user_id_str = (meta.get("user_id") or "").strip()
    plan = plan_from_metadata({"metadata": meta, **data})
    customer = data.get("customer") or {}
    customer_id = customer.get("customer_id") or data.get("customer_id") or ""
    email = (customer.get("email") or "").strip().lower()

    user: User | None = None
    if user_id_str:
        try:
            user = db.get(User, uuid.UUID(user_id_str))
        except ValueError:
            user = None
    if not user and email:
        user = db.query(User).filter(User.email == email).one_or_none()
    if not user:
        return JSONResponse({"ok": True, "ignored": "no user"})

    billing = _resolve_billing_for_webhook(db, user, meta if isinstance(meta, dict) else {})
    if customer_id:
        billing.bachs_customer_id = str(customer_id)
    checkout_id = data.get("checkout_id") or ""
    if checkout_id:
        billing.last_checkout_id = str(checkout_id)

    if etype in {"checkout.completed", "collection.succeeded"}:
        sub = data.get("subscription") or {}
        if isinstance(sub, dict) and sub.get("subscription_id"):
            billing.bachs_subscription_id = str(sub["subscription_id"])
            status = str(sub.get("status") or "active")
            chosen = plan or (
                billing.plan if billing.plan in PAID_PLANS else BillingPlan.PLATFORM_MONTHLY.value
            )
            _apply_paid_plan(billing, chosen, status)
        elif plan in PAID_PLANS:
            _apply_paid_plan(billing, plan, "active")

    elif etype in {
        "customer.subscription.created",
        "customer.subscription.updated",
    }:
        billing.bachs_subscription_id = str(
            data.get("subscription_id") or billing.bachs_subscription_id
        )
        status = str(data.get("status") or "active")
        billing.subscription_status = status
        if plan in PAID_PLANS:
            billing.plan = plan
        elif billing.plan not in PAID_PLANS:
            billing.plan = BillingPlan.PLATFORM_MONTHLY.value
        if status in {"canceled", "unpaid", "paused"}:
            billing.plan = BillingPlan.NONE.value

    elif etype == "customer.subscription.deleted":
        billing.subscription_status = "canceled"
        if billing.plan in PAID_PLANS:
            billing.plan = BillingPlan.NONE.value

    db.add(billing)
    db.commit()
    return {"ok": True}
