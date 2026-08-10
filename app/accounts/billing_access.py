from __future__ import annotations

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

# Clear matches free profiles may open before upgrading.
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


def free_clear_listing_ids(db: Session, user: User) -> set[int]:
    """Top matched listing ids a free profile may open (by default match threshold)."""
    from app.accounts.settings import load_user_settings
    from app.services import list_job_cards

    cfg = load_user_settings(db, user)
    threshold = float((cfg.get("search") or {}).get("min_match_score") or 65)
    cards = list_job_cards(db, user, min_score=threshold)
    return {int(c.id) for c in cards[:FREE_CLEAR_MATCHES]}


def can_open_listing(db: Session, user: User, listing_id: int) -> tuple[bool, str]:
    """Staff/paid profile: any visible role. Free: top matches only; own pastes always ok."""
    ensure_account(db, user)
    listing = db.get(JobListing, listing_id)
    if listing is None:
        return False, "Job not found"
    if (
        listing.visibility == ListingVisibility.PRIVATE.value
        and listing.owner_user_id == user.id
    ):
        return True, "private"
    if has_full_job_access(db, user):
        return True, "full"
    if listing_id in free_clear_listing_ids(db, user):
        return True, "preview"
    return False, "Upgrade this profile on Billing to unlock more matched roles."


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
    """Prefer edge geo headers; fall back to Accept-Language, then region cookie."""
    region, _ = detect_billing_region_detail(request_headers, cookies)
    return region


def detect_billing_region_detail(
    request_headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
) -> tuple[str, str]:
    """
    Returns (region, source).
    source: 'geo' | 'locale' | 'cookie' | 'default'
    """
    headers = {k.lower(): v for k, v in (request_headers or {}).items()}
    country = (
        headers.get("cf-ipcountry")
        or headers.get("cloudfront-viewer-country")
        or headers.get("x-vercel-ip-country")
        or headers.get("x-country-code")
        or ""
    ).strip().upper()
    if country and country not in {"XX", "T1", "A1", "A2", "O1"}:
        region = BillingRegion.NG.value if country == "NG" else BillingRegion.INTL.value
        return region, "geo"

    accept = headers.get("accept-language") or ""
    for part in accept.split(","):
        tag = part.split(";")[0].strip().lower().replace("_", "-")
        if tag == "ng" or tag.endswith("-ng"):
            return BillingRegion.NG.value, "locale"

    cookie = ((cookies or {}).get("vitae_region") or "").strip().lower()
    if cookie in {BillingRegion.NG.value, BillingRegion.INTL.value}:
        return cookie, "cookie"
    return BillingRegion.INTL.value, "default"
