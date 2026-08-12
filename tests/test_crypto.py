from __future__ import annotations

from app.config import get_settings
from app.crypto import decrypt_secret, encrypt_secret


def test_encrypt_decrypt_roundtrip(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "jwt-secret-aaaaaaaaaaaaaaaa")
    monkeypatch.delenv("FERNET_SECRET_KEY", raising=False)
    get_settings.cache_clear()
    token = encrypt_secret("sk-test-key")
    assert token
    assert decrypt_secret(token) == "sk-test-key"
    get_settings.cache_clear()


def test_fernet_key_preferred_for_new_ciphertext(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "jwt-secret-bbbbbbbbbbbbbbbb")
    monkeypatch.setenv("FERNET_SECRET_KEY", "fernet-secret-cccccccccccccccc")
    get_settings.cache_clear()
    token = encrypt_secret("sk-byok")
    assert decrypt_secret(token) == "sk-byok"
    get_settings.cache_clear()


def test_legacy_secret_key_ciphertext_still_decrypts(monkeypatch):
    # Encrypt under SECRET_KEY-only (legacy)
    monkeypatch.setenv("SECRET_KEY", "legacy-jwt-secret-dddddddddd")
    monkeypatch.delenv("FERNET_SECRET_KEY", raising=False)
    get_settings.cache_clear()
    legacy_token = encrypt_secret("sk-old")

    # Split keys: primary Fernet differs; legacy decrypt falls back to SECRET_KEY
    monkeypatch.setenv("FERNET_SECRET_KEY", "new-fernet-eeeeeeeeeeeeeeee")
    get_settings.cache_clear()
    assert decrypt_secret(legacy_token) == "sk-old"
    get_settings.cache_clear()
