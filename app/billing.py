from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import httpx

from app.config import get_settings

PAID_PLANS = frozenset({"byok_monthly", "platform_monthly"})


def bachs_base_url() -> str:
    s = get_settings()
    if (s.bachs_api_base or "").strip():
        return s.bachs_api_base.rstrip("/")
    key = s.bachs_api_key or ""
    if key.startswith("sk_live_"):
        return "https://api.bachs.io"
    return "https://sandbox-api.bachs.io"


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
    """Create a Bachs checkout for BYOK or Platform monthly subscription."""
    s = get_settings()
    if not s.bachs_api_key:
        raise RuntimeError("BACHS_API_KEY is not configured")
    if plan not in PAID_PLANS:
        raise ValueError(f"Invalid plan: {plan}")

    product_id = _product_id_for(plan, region)
    payload: dict[str, Any] = {
        "customer": {"email": email, "name": name or email},
        "success_url": success_url,
        "cancel_url": cancel_url,
        "metadata": metadata or {},
    }
    if product_id:
        payload["product_cart"] = [{"product_id": product_id, "quantity": 1}]
    else:
        amount, currency = _adhoc_price(plan, region)
        payload["pricing"] = {
            "price_type": "fixed",
            "amount": amount,
            "currency": currency,
            "billing_cycle": {"interval": "month", "frequency": 1},
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
        resp.raise_for_status()
        return resp.json()


def _product_id_for(plan: str, region: str) -> str:
    s = get_settings()
    mapping = {
        ("byok_monthly", "ng"): s.bachs_product_byok_ng,
        ("platform_monthly", "ng"): s.bachs_product_platform_ng,
        ("byok_monthly", "intl"): s.bachs_product_byok_intl,
        ("platform_monthly", "intl"): s.bachs_product_platform_intl,
    }
    return (mapping.get((plan, region)) or "").strip()


def _adhoc_price(plan: str, region: str) -> tuple[str, str]:
    """BYOK ₦2000 / $5 · Platform ₦5000 / $10 — both monthly."""
    if region == "ng":
        if plan == "byok_monthly":
            return "2000.00", "NGN"
        return "5000.00", "NGN"
    if plan == "byok_monthly":
        return "5.00", "USD"
    return "10.00", "USD"


def verify_bachs_webhook(
    raw_body: bytes,
    signature_header: str,
    timestamp_header: str = "",
    *,
    max_skew_seconds: int = 300,
) -> bool:
    """HMAC-SHA256 of '{timestamp}.{raw_body}'. Fail closed unless secret or DEV accept."""
    s = get_settings()
    secret = (s.bachs_webhook_secret or "").strip()
    if not secret:
        return bool(s.bachs_webhook_dev_accept)
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
    # Accept legacy checkout metadata briefly
    if plan == "byok_lifetime":
        plan = "byok_monthly"
    if plan in PAID_PLANS:
        return plan
    return ""
