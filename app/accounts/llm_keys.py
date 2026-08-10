"""Multi-provider BYOK key storage helpers (encrypted map on ProfileBilling)."""

from __future__ import annotations

import json
from typing import Any

from app.llm_providers import LLM_PROVIDERS, PROVIDER_BY_ID, PROVIDERS
from app.models import ProfileBilling

PROVIDER_META: dict[str, dict[str, str]] = {
    p.id: {
        "label": p.label,
        "placeholder": p.placeholder,
        "hint": p.hint,
        "logo": p.logo,
    }
    for p in PROVIDERS
}


def load_llm_keys_map(billing: ProfileBilling | None) -> dict[str, str]:
    """Return provider → ciphertext. Migrates legacy single-key values."""
    if not billing:
        return {}
    raw = (billing.llm_key_encrypted or "").strip()
    if not raw:
        return {}
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            out: dict[str, str] = {}
            for key, value in data.items():
                name = str(key).strip().lower()
                if name in PROVIDER_BY_ID and isinstance(value, str) and value.strip():
                    out[name] = value.strip()
            return out
    provider = (billing.llm_provider or "openai").strip().lower()
    if provider not in PROVIDER_BY_ID:
        provider = "openai"
    return {provider: raw}


def dump_llm_keys_map(keys: dict[str, str]) -> str:
    cleaned = {
        name: token.strip()
        for name, token in keys.items()
        if name in PROVIDER_BY_ID and isinstance(token, str) and token.strip()
    }
    if not cleaned:
        return ""
    return json.dumps(cleaned, separators=(",", ":"), sort_keys=True)


def has_llm_keys(billing: ProfileBilling | None) -> bool:
    return bool(load_llm_keys_map(billing))


def saved_providers(billing: ProfileBilling | None) -> set[str]:
    return set(load_llm_keys_map(billing))


def resolve_active_key(billing: ProfileBilling | None) -> tuple[str, str]:
    """Pick (provider, ciphertext) preferring llm_provider when that key exists."""
    keys = load_llm_keys_map(billing)
    if not keys:
        preferred = ((billing.llm_provider if billing else "") or "openai").strip().lower()
        if preferred not in PROVIDER_BY_ID:
            preferred = "openai"
        return preferred, ""
    preferred = ((billing.llm_provider if billing else "") or "").strip().lower()
    if preferred in keys:
        return preferred, keys[preferred]
    for name in LLM_PROVIDERS:
        if name in keys:
            return name, keys[name]
    name, token = next(iter(keys.items()))
    return name, token


def apply_llm_key_updates(
    billing: ProfileBilling,
    *,
    active_provider: str,
    new_keys: dict[str, str],
    clear: set[str] | None = None,
) -> None:
    """Merge form updates into billing; empty new_keys leave existing; clear removes."""
    keys = load_llm_keys_map(billing)
    for name in clear or set():
        if name in PROVIDER_BY_ID:
            keys.pop(name, None)
    for name, plaintext in (new_keys or {}).items():
        if name not in PROVIDER_BY_ID:
            continue
        text = (plaintext or "").strip()
        if not text:
            continue
        from app.crypto import encrypt_secret

        keys[name] = encrypt_secret(text)
    provider = (active_provider or "").strip().lower()
    if provider not in PROVIDER_BY_ID:
        provider = "openai"
    if provider not in keys and keys:
        provider = next(p for p in LLM_PROVIDERS if p in keys)
    billing.llm_provider = provider
    billing.llm_key_encrypted = dump_llm_keys_map(keys)


def providers_for_template(billing: ProfileBilling | None) -> list[dict[str, Any]]:
    saved = saved_providers(billing)
    active, _ = resolve_active_key(billing)
    rows: list[dict[str, Any]] = []
    for spec in PROVIDERS:
        rows.append(
            {
                "id": spec.id,
                "label": spec.label,
                "placeholder": spec.placeholder,
                "hint": spec.hint,
                "logo": spec.logo,
                "saved": spec.id in saved,
                "active": spec.id == active,
            }
        )
    return rows
