from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.accounts.bootstrap import ensure_account
from app.accounts.llm_keys import has_llm_keys, resolve_active_key
from app.accounts.profile import get_active_profile, get_profile_billing
from app.models import (
    BillingRegion,
    JobListing,
    ListingVisibility,
    ProfileBilling,
    User,
)

_ACTIVE_SUB = frozenset({"active", "trialing"})

# One-time free listing opens per career profile (not monthly / not rotating).
FREE_CLEAR_MATCHES = 3

# Every feature is open to every signed-in user. There is no paywall, no AI
# credit pool and no listing quota, so the access helpers below always allow.
# Kept as functions (rather than deleted) because call sites and templates still
# read them — returning True here is what unlocks the UI.
ALL_FEATURES_OPEN = True


def _subscription_active(billing: ProfileBilling) -> bool:
    return (billing.subscription_status or "").lower() in _ACTIVE_SUB


def can_use_ai(db: Session, user: User) -> tuple[bool, str]:
    """AI is available to every user. Returns (allowed, mode) for key resolution."""
    ensure_account(db, user)
    billing = get_profile_billing(db, user)
    # A user's own key is preferred when they saved one; otherwise the
    # server-owned platform keys (OpenAI / Gemini / Groq) are used.
    if has_llm_keys(billing):
        return True, "byok"
    from app.llm import platform_creds

    if platform_creds():
        return True, "platform"
    return False, "Add an LLM API key in Settings, or configure platform keys on the server."


def has_full_job_access(db: Session, user: User) -> bool:
    """Every user sees the full job catalogue."""
    return True


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


def listing_board_is_clear(db: Session, user: User, listing_id: int) -> bool:
    """No blurred rows: every visible listing shows its title/company."""
    return db.get(JobListing, listing_id) is not None


def listing_is_free_openable(db: Session, user: User, listing_id: int) -> bool:
    """Every visible listing can be opened — there is no quota."""
    return db.get(JobListing, listing_id) is not None


def can_open_listing(db: Session, user: User, listing_id: int) -> tuple[bool, str]:
    """Any visible listing is open. Only genuine availability checks apply."""
    ensure_account(db, user)
    listing = db.get(JobListing, listing_id)
    if listing is None:
        return False, "Job not found"
    if (
        listing.visibility == ListingVisibility.PRIVATE.value
        and listing.owner_user_id != user.id
    ):
        return False, "This listing is private."
    if not listing.is_active and listing.visibility == ListingVisibility.PUBLIC.value:
        return False, "This listing is no longer open."
    return True, "open"


def llm_creds_for_user(db: Session, user: User):
    """Resolve LLM credentials: the user's own key if saved, else platform keys."""
    from app.llm import byok_creds, platform_creds

    ok, mode = can_use_ai(db, user)
    if not ok:
        return None
    billing = get_profile_billing(db, user)
    if mode == "byok":
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
    X-Country-Code. Edge geo headers (CF / CloudFront / Vercel) are trusted by
    default; set TRUST_EDGE_GEO=0 to ignore them (e.g. local without a real edge).
    """
    from app.config import get_settings

    _ = cookies  # intentionally unused — never trust client region cookie for pricing
    s = get_settings()
    if not bool(getattr(s, "trust_edge_geo", True)):
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
