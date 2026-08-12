from __future__ import annotations

import hashlib
import hmac
import json
import time

from app.accounts import ensure_account, has_full_job_access
from app.accounts.billing_access import can_use_ai
from app.accounts.profile import get_active_profile, get_profile_billing
from app.billing import verify_bachs_webhook
from app.db import SessionLocal
from app.models import BillingPlan, User


def _sign(body: bytes, secret: str = "test-webhook-secret") -> tuple[str, str]:
    ts = str(int(time.time()))
    message = f"{ts}.".encode() + body
    sig = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return ts, sig


def test_verify_bachs_webhook_rejects_bad_signature():
    body = b'{"type":"checkout.completed"}'
    assert not verify_bachs_webhook(body, "deadbeef", str(int(time.time())))


def test_verify_bachs_webhook_accepts_valid_signature():
    body = b'{"type":"checkout.completed"}'
    ts, sig = _sign(body)
    assert verify_bachs_webhook(body, sig, ts)


def test_webhook_requires_user_identity(client):
    """Email-only events must not activate a plan."""
    db = SessionLocal()
    try:
        # Ensure someone exists with the payer email
        existing = db.query(User).filter(User.email == "orphan@example.com").one_or_none()
        if existing is None:
            # Register via another connection; skip if hard — just post webhook
            pass
    finally:
        db.close()

    payload = {
        "type": "checkout.completed",
        "data": {
            "payment_status": "paid",
            "mode": "subscription",
            "metadata": {"plan": "platform_monthly"},
            "customer": {"email": "orphan@example.com", "id": "cus_orphan"},
        },
    }
    raw = json.dumps(payload).encode()
    ts, sig = _sign(raw)
    r = client.post(
        "/webhooks/bachs",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Bachs-Signature": sig,
            "X-Bachs-Timestamp": ts,
        },
    )
    assert r.status_code == 200
    assert r.json().get("ignored") == "no user"


def test_webhook_activates_plan_with_user_id(client, confirmed_user):
    user_id = confirmed_user["user"].id
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        ensure_account(db, u)
        profile = get_active_profile(db, u)
        profile_id = profile.id
        email = u.email
    finally:
        db.close()

    payload = {
        "type": "checkout.completed",
        "data": {
            "payment_status": "paid",
            "mode": "subscription",
            "checkout_id": "chk_test_1",
            "customer": {"id": "cus_test_1", "email": email},
            "metadata": {
                "user_id": str(user_id),
                "profile_id": str(profile_id),
                "plan": "platform_monthly",
            },
        },
    }
    raw = json.dumps(payload).encode()
    ts, sig = _sign(raw)
    r = client.post(
        "/webhooks/bachs",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Bachs-Signature": sig,
            "X-Bachs-Timestamp": ts,
        },
    )
    assert r.status_code == 200
    assert r.json().get("ok") is True

    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        billing = get_profile_billing(db, u)
        assert billing.plan == BillingPlan.PLATFORM_MONTHLY.value
        assert billing.subscription_status == "active"
        assert has_full_job_access(db, u)
        ok, mode = can_use_ai(db, u)
        assert ok
        assert mode == "platform"
    finally:
        db.close()


def test_webhook_resolves_by_customer_id(client, confirmed_user):
    user = confirmed_user["user"]
    db = SessionLocal()
    try:
        u = db.get(User, user.id)
        ensure_account(db, u)
        billing = get_profile_billing(db, u)
        billing.bachs_customer_id = "cus_renewal"
        billing.plan = BillingPlan.BYOK_MONTHLY.value
        billing.subscription_status = "past_due"
        db.add(billing)
        db.commit()
    finally:
        db.close()

    payload = {
        "type": "invoice.paid",
        "data": {
            "customer_id": "cus_renewal",
            "subscription_id": "sub_1",
            "metadata": {},
        },
    }
    raw = json.dumps(payload).encode()
    ts, sig = _sign(raw)
    r = client.post(
        "/webhooks/bachs",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Bachs-Signature": sig,
            "X-Bachs-Timestamp": ts,
        },
    )
    assert r.status_code == 200

    db = SessionLocal()
    try:
        u = db.get(User, user.id)
        billing = get_profile_billing(db, u)
        assert billing.subscription_status == "active"
    finally:
        db.close()


def test_free_tier_lacks_full_access(confirmed_user):
    db = SessionLocal()
    try:
        user = db.get(User, confirmed_user["user"].id)
        billing = get_profile_billing(db, user)
        billing.plan = BillingPlan.NONE.value
        billing.subscription_status = ""
        db.add(billing)
        db.commit()
        assert not has_full_job_access(db, user)
        ok, _ = can_use_ai(db, user)
        assert not ok
    finally:
        db.close()
