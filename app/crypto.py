from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


def _fernet_from_secret(secret: str) -> Fernet:
    digest = hashlib.sha256(secret.encode()).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def _primary_secret() -> str:
    from app.config import _is_test_env

    s = get_settings()
    fernet = (s.fernet_secret_key or "").strip()
    if fernet:
        return fernet
    # In non-test environments FERNET_SECRET_KEY is required (assert_secure_settings
    # enforces this at startup).  In tests fall back to secret_key so unit tests
    # that set only SECRET_KEY still work.
    secret = (s.secret_key or "").strip()
    if secret:
        return secret
    if not _is_test_env():
        raise RuntimeError(
            "FERNET_SECRET_KEY (or SECRET_KEY as fallback) must be set. "
            "Run assert_secure_settings() at startup to catch this earlier."
        )
    return "test-only-insecure-placeholder"


def _legacy_secret() -> str | None:
    """When FERNET_SECRET_KEY is set, SECRET_KEY is the legacy decrypt-only path."""
    s = get_settings()
    fernet = (s.fernet_secret_key or "").strip()
    if not fernet:
        return None
    legacy = (s.secret_key or "").strip()
    if not legacy or legacy == fernet:
        return None
    return legacy


def _primary_fernet() -> Fernet:
    return _fernet_from_secret(_primary_secret())


def encrypt_secret(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _primary_fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    try:
        return _primary_fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        pass
    legacy = _legacy_secret()
    if legacy:
        try:
            return _fernet_from_secret(legacy).decrypt(token.encode()).decode()
        except InvalidToken:
            return ""
    return ""
