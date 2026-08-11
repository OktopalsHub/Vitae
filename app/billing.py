from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import httpx

from app.config import get_settings

PAID_PLANS = frozenset({"byok_monthly", "platform_monthly"})

# Four recurring products:
#   NG  → NGN ₦2,000 (BYOK) / ₦5,000 (Platform)
#   intl → USD $5 (BYOK) / $10 (Platform)
# See https://docs.bachs.io/guides/subscriptions/overview
_CARD_ONLY = ["card"]

DISPLAY_PRICES: dict[tuple[str, str], str] = {
    ("byok_monthly", "ng"): "₦2,000",
    ("platform_monthly", "ng"): "₦5,000",
    ("byok_monthly", "intl"): "$5",
    ("platform_monthly", "intl"): "$10",
}

# Expected catalog amounts when creating products in Bachs.
CATALOG_AMOUNTS: dict[tuple[str, str], tuple[str, str]] = {
    ("byok_monthly", "ng"): ("2000.00", "NGN"),
    ("platform_monthly", "ng"): ("5000.00", "NGN"),
    ("byok_monthly", "intl"): ("5.00", "USD"),
    ("platform_monthly", "intl"): ("10.00", "USD"),
}


def bachs_base_url() -> str:
    s = get_settings()
    if (s.bachs_api_base or "").strip():
        return s.bachs_api_base.rstrip("/")
    key = s.bachs_api_key or ""
    if key.startswith("sk_live_"):
        return "https://api.bachs.io"
    return "https://sandbox-api.bachs.io"


def billing_currency_for(region: str) -> str:
    return "NGN" if region == "ng" else "USD"


async def create_checkout_session(
    *,
    email: str,
    name: str,
    plan: str,
    region: str,
    success_url: str,
    cancel_url: str,
    metadata: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create a Bachs subscription checkout for BYOK or Platform monthly.

    Geo picks among four products: NG NGN (₦2k / ₦5k) or intl USD ($5 / $10).
    """
    s = get_settings()
    if not s.bachs_api_key:
        raise RuntimeError("BACHS_API_KEY is not configured")
    if plan not in PAID_PLANS:
        raise ValueError(f"Invalid plan: {plan}")

    product_id = product_id_for(plan, region)
    if not product_id:
        raise RuntimeError(
            "No Bachs recurring product configured for this plan/region. "
            "Set BACHS_PRODUCT_BYOK_NG + BACHS_PRODUCT_PLATFORM_NG (₦2,000 / ₦5,000 NGN) "
            "and BACHS_PRODUCT_BYOK_INTL + BACHS_PRODUCT_PLATFORM_INTL ($5 / $10 USD)."
        )

    currency = billing_currency_for(region)
    meta = dict(metadata or {})
    meta.setdefault("bachs_product_id", product_id)
    meta.setdefault("billing_region", region)
    meta.setdefault("billing_currency", currency)

    payload: dict[str, Any] = {
        "customer": {"email": email, "name": name or email},
        "product_cart": [{"product_id": product_id, "quantity": 1}],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "metadata": meta,
        "billing_currency": currency,
        # Card for USD subs; leave open for NGN when Bachs enables local rails.
        "allowed_payment_method_types": list(_CARD_ONLY),
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{bachs_base_url()}/v1/checkout-sessions",
            headers={
                "Authorization": f"Bearer {s.bachs_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if resp.is_error:
            detail = _bachs_error_detail(resp)
            raise RuntimeError(f"Bachs checkout failed ({resp.status_code}): {detail}")
        return resp.json()


def _bachs_error_detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        return (resp.text or "")[:400]
    if isinstance(body, dict):
        return str(body.get("detail") or body.get("message") or body)[:400]
    return str(body)[:400]


def product_id_for(plan: str, region: str) -> str:
    """Resolve product ID for plan + region. NG does not fall back to USD intl."""
    s = get_settings()
    mapping = {
        ("byok_monthly", "ng"): s.bachs_product_byok_ng,
        ("platform_monthly", "ng"): s.bachs_product_platform_ng,
        ("byok_monthly", "intl"): s.bachs_product_byok_intl,
        ("platform_monthly", "intl"): s.bachs_product_platform_intl,
    }
    return (mapping.get((plan, region)) or "").strip()


def has_ng_products() -> bool:
    s = get_settings()
    return bool(
        (s.bachs_product_byok_ng or "").strip()
        and (s.bachs_product_platform_ng or "").strip()
    )


def checkout_note_for(region: str) -> str:
    if region == "ng":
        if has_ng_products():
            return "Nigeria pricing · ₦2,000 / ₦5,000 per month via Bachs. Cancel anytime."
        return (
            "Set BACHS_PRODUCT_BYOK_NG and BACHS_PRODUCT_PLATFORM_NG "
            "(₦2,000 / ₦5,000 NGN products) to enable Nigeria checkout."
        )
    return "International pricing · $5 / $10 per month · billed in USD via Bachs."


def display_price(plan: str, region: str) -> str:
    return DISPLAY_PRICES.get((plan, region)) or DISPLAY_PRICES[(plan, "intl")]


def verify_bachs_webhook(
    raw_body: bytes,
    signature_header: str,
    timestamp_header: str = "",
    *,
    max_skew_seconds: int = 300,
) -> bool:
    """HMAC-SHA256 of '{timestamp}.{raw_body}'. Fail closed in production."""
    s = get_settings()
    secret = (s.bachs_webhook_secret or "").strip()
    env = (s.app_env or "").strip().lower()
    prod_like = env in {"production", "prod", "cloud"}
    if not secret:
        if prod_like:
            return False
        return bool(s.bachs_webhook_dev_accept) and env in {
            "development",
            "dev",
            "test",
            "",
        }
    if not signature_header or not timestamp_header:
        return False
    try:
        ts = int(timestamp_header.strip())
    except ValueError:
        return False
    if abs(int(time.time()) - ts) > max_skew_seconds:
        return False
    message = f"{timestamp_header.strip()}.".encode() + raw_body
    digest = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    candidates = [signature_header.strip()]
    if signature_header.startswith("sha256="):
        candidates.append(signature_header.removeprefix("sha256=").strip())
    return any(hmac.compare_digest(digest, c) for c in candidates)


def plan_from_metadata(data: dict[str, Any]) -> str:
    meta = data.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except json.JSONDecodeError:
            meta = {}
    plan = (meta.get("plan") or data.get("plan") or "").strip()
    if plan == "byok_lifetime":
        plan = "byok_monthly"
    if plan in PAID_PLANS:
        return plan
    return ""


def checkout_redirect_url(session: dict[str, Any]) -> tuple[str, str]:
    """Return (checkout_id, checkout_url) from a create-session response."""
    data = session.get("data") if isinstance(session.get("data"), dict) else session
    if not isinstance(data, dict):
        return "", ""
    checkout_id = str(data.get("checkout_id") or data.get("id") or "")
    url = str(data.get("checkout_url") or data.get("url") or data.get("link") or "")
    return checkout_id, url
