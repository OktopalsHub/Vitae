import hashlib
import json

from app.models import BillingEvent

def test_billing_event_is_unique(db_session):
    payload = {"id": "evt-1", "type": "invoice.paid"}
    raw = json.dumps(payload).encode()
    event = BillingEvent(
        provider="bachs",
        event_id="evt-1",
        event_type="invoice.paid",
        payload_hash=hashlib.sha256(raw).hexdigest(),
        payload_json=raw.decode(),
    )
    db_session.add(event)
    db_session.commit()
    duplicate = BillingEvent(
        provider="bachs",
        event_id="evt-1",
        event_type="invoice.paid",
        payload_hash=hashlib.sha256(raw).hexdigest(),
        payload_json=raw.decode(),
    )
    db_session.add(duplicate)
    try:
        db_session.commit()
        assert False, "duplicate billing event should fail"
    except Exception:
        db_session.rollback()
