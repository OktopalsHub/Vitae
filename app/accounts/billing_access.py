from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.accounts.bootstrap import ensure_account
from app.accounts.llm_keys import has_llm_keys, resolve_active_key
from app.accounts.profile import get_active_profile, get_profile_billing
from app.models import (
    BillingPlan,
    BillingRegion,
    JobListing,
    ListingVisibility,
    ProfileBilling,
    User,
)
from app.roles import is_admin

_ACTIVE_SUB = frozenset({"active", "trialing"})
_PAID_PLANS = frozenset(
    {
        BillingPlan.BYOK_MONTHLY.value,
        BillingPlan.PLATFORM_MONTHLY.value,
    }
)

# One-time free listing opens per career profile (not monthly / not rotating).
FREE_CLEAR_MATCHES = 3


def _subscription_active(billing: ProfileBilling) -> bool:
    return (billing.subscription_status or "").lower() in _ACTIVE_SUB


def can_use_ai(db: Session, user: User) -> tuple[bool, str]:
    """Returns (allowed, reason/mode) for the active profile's subscription."""
    ensure_account(db, user)
    billing = get_profile_billing(db, user)

    if is_admin(user):
        if has_llm_keys(billing):
            return True, "staff_byok"
        from app.llm import platform_creds

        if platform_creds():
            return True, "staff_platform"
        return False, "Add your LLM API key in Settings, or configure platform keys on the server."

    if not _subscription_active(billing):
        if billing.plan in _PAID_PLANS:
            return False, "Your subscription is not active. Renew on Billing."
        return False, "Choose a plan on Billing to unlock AI for this profile."

    if billing.plan == BillingPlan.BYOK_MONTHLY.value:
        if not has_llm_keys(billing):
            return False, "Add at least one LLM API key in Settings (BYOK plan)."
        return True, "byok"
    if billing.plan == BillingPlan.PLATFORM_MONTHLY.value:
        return True, "platform"
    return False, "Choose a plan on Billing to unlock AI for this profile."


def has_full_job_access(db: Session, user: User) -> bool:
    """Paid active plan on the active profile, or staff."""
    ensure_account(db, user)
    if is_admin(user):
        return True
    billing = get_profile_billing(db, user)
    return billing.plan in _PAID_PLANS and _subscription_active(billing)


def free_unlocked_listing_ids(db: Session, user: User) -> set[int]:
    """Listing ids this free profile has already opened (one-time grant)."""
    ensure_account(db, user)
    profile = get_active_profile(db, user)
    if profile is None:
        return set()
    try:
        raw = json.loads(profile.free_unlocked_json or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        raw = []
    out: set[int] = set()
    if not isinstance(raw, list):
        return out
    for item in raw:
        try:
            out.add(int(item))
        except (TypeError, ValueError):
            continue
    return out


def free_opens_remaining(db: Session, user: User) -> int:
    return max(0, FREE_CLEAR_MATCHES - len(free_unlocked_listing_ids(db, user)))


def free_clear_listing_ids(db: Session, user: User) -> set[int]:
    """Back-compat alias: listings already claimed under the free grant."""
    return free_unlocked_listing_ids(db, user)


def claim_free_listing_open(db: Session, user: User, listing_id: int) -> bool:
    """Claim one one-time free open. True if unlocked (already or newly claimed)."""
    ensure_account(db, user)
    profile = get_active_profile(db, user)
    if profile is None:
        return False
    unlocked = free_unlocked_listing_ids(db, user)
    if listing_id in unlocked:
        return True
    if len(unlocked) >= FREE_CLEAR_MATCHES:
        return False
    unlocked.add(int(listing_id))
    profile.free_unlocked_json = json.dumps(sorted(unlocked))
    db.add(profile)
    db.flush()
    return True


def listing_is_free_openable(db: Session, user: User, listing_id: int) -> bool:
    """Board gate: own pastes, already unlocked, or free opens remain (claim on open)."""
    listing = db.get(JobListing, listing_id)
    if listing is None:
        return False
    if (
        listing.visibility == ListingVisibility.PRIVATE.value
        and listing.owner_user_id == user.id
    ):
        return True
    unlocked = free_unlocked_listing_ids(db, user)
    if listing_id in unlocked:
        return True
    return len(unlocked) < FREE_CLEAR_MATCHES


def can_open_listing(db: Session, user: User, listing_id: int) -> tuple[bool, str]:
    """Staff/paid profile: any visible role. Free: first N opens ever; own pastes always ok."""
    ensure_account(db, user)
    listing = db.get(JobListing, listing_id)
    if listing is None:
        return False, "Job not found"
    if (
        listing.visibility == ListingVisibility.PRIVATE.value
        and listing.owner_user_id == user.id
    ):
        return True, "private"
    if not listing.is_active and listing.visibility == ListingVisibility.PUBLIC.value:
        return False, "This listing is no longer open."
    if has_full_job_access(db, user):
        return True, "full"
    if claim_free_listing_open(db, user, listing_id):
        return True, "preview"
    return False, "You've used your free openings. Subscribe on Billing to unlock more roles."


def llm_creds_for_user(db: Session, user: User):
    """Resolve LLM credentials from the active profile's plan / keys."""
    from app.llm import byok_creds, platform_creds

    ok, mode = can_use_ai(db, user)
    if not ok:
        return None
    billing = get_profile_billing(db, user)
    if mode in {"byok", "staff_byok"}:
        provider, encrypted = resolve_active_key(billing)
        return byok_creds(encrypted, provider)
    return platform_creds()


def detect_billing_region(
    request_headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
) -> str:
    """Server-side region for pricing. Client cookies/locale cannot set NG pricing."""
    region, _ = detect_billing_region_detail(request_headers, cookies)
    return region


def detect_billing_region_detail(
    request_headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
) -> tuple[str, str]:
    """
    Returns (region, source).

    Pricing never trusts Accept-Language, vitae_region cookie, or spoofable
    X-Country-Code. Edge geo headers are used only when TRUST_EDGE_GEO=1
    (Cloudflare / CloudFront / Vercel set these; clients cannot).
    Otherwise default to intl.
    """
    from app.config import get_settings

    _ = cookies  # intentionally unused — never trust client region cookie for pricing
    s = get_settings()
    if not bool(getattr(s, "trust_edge_geo", False)):
        return BillingRegion.INTL.value, "default"

    headers = {k.lower(): v for k, v in (request_headers or {}).items()}
    # Only headers typically injected by the edge — not X-Country-Code.
    country = (
        headers.get("cf-ipcountry")
        or headers.get("cloudfront-viewer-country")
        or headers.get("x-vercel-ip-country")
        or ""
    ).strip().upper()
    if country and country not in {"XX", "T1", "A1", "A2", "O1"}:
        region = BillingRegion.NG.value if country == "NG" else BillingRegion.INTL.value
        return region, "geo"
    return BillingRegion.INTL.value, "default"
