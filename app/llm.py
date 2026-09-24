from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import time

from app.config import get_settings
from app.ai.safety import AI_SAFETY_SYSTEM
from app.crypto import decrypt_secret
from app.llm_providers import PLATFORM_PROVIDERS, ProviderSpec, get_provider

# Bound every LLM call. Without this, provider SDK defaults are ~600s timeout
# with 2 retries — one bad endpoint could stall a request for 30 minutes.
LLM_TIMEOUT_SECONDS = 90.0
LLM_MAX_RETRIES = 1
DEFAULT_PROMPT_VERSION = "v1"
logger = logging.getLogger(__name__)

# Gemini 2.0 Flash shut down 2026-06-01. Remap so stale .env / Cloud vars still work.
_RETIRED_GEMINI_MODELS = {
    "gemini-2.0-flash": "gemini-2.5-flash",
    "gemini-2.0-flash-001": "gemini-2.5-flash",
    "gemini-2.0-flash-exp": "gemini-2.5-flash",
    "gemini-2.0-flash-lite": "gemini-2.5-flash-lite",
    "gemini-2.0-flash-lite-001": "gemini-2.5-flash-lite",
}


def resolve_gemini_model(model: str) -> str:
    raw = (model or "").strip()
    key = raw.lower().removeprefix("models/")
    return _RETIRED_GEMINI_MODELS.get(key) or raw or "gemini-2.5-flash"


@dataclass
class LLMCreds:
    provider: str
    api_key: str
    model: str = ""
    base_url: str = ""
    kind: str = "openai"
    # Back-compat aliases
    openai_model: str = ""
    anthropic_model: str = ""
    gemini_model: str = ""

    def __post_init__(self) -> None:
        if not self.model:
            if self.kind == "anthropic" and self.anthropic_model:
                self.model = self.anthropic_model
            elif self.kind == "gemini" and self.gemini_model:
                self.model = self.gemini_model
            elif self.openai_model:
                self.model = self.openai_model
        if self.kind == "openai" and not self.openai_model:
            self.openai_model = self.model
        if self.kind == "anthropic" and not self.anthropic_model:
            self.anthropic_model = self.model
        if self.kind == "gemini":
            resolved = resolve_gemini_model(self.model or self.gemini_model)
            self.model = resolved
            self.gemini_model = resolved


def _settings_key(spec: ProviderSpec) -> str:
    if not spec.env_key_attr:
        return ""
    s = get_settings()
    return (getattr(s, spec.env_key_attr, "") or "").strip()


def _model_for_spec(spec: ProviderSpec) -> str:
    """Platform OpenAI/Gemini can override model via .env; BYOK uses registry defaults."""
    model = spec.default_model
    if spec.env_model_attr:
        s = get_settings()
        override = (getattr(s, spec.env_model_attr, "") or "").strip()
        if override:
            model = override
    if spec.kind == "gemini":
        return resolve_gemini_model(model)
    return model


def _creds_from_spec(spec: ProviderSpec, api_key: str) -> LLMCreds:
    model = _model_for_spec(spec)
    return LLMCreds(
        provider=spec.id,
        api_key=api_key,
        model=model,
        base_url=spec.base_url,
        kind=spec.kind,
        openai_model=model if spec.kind in {"openai", "openai_compat"} else "",
        anthropic_model=model if spec.kind == "anthropic" else "",
        gemini_model=model if spec.kind == "gemini" else "",
    )


def platform_creds() -> LLMCreds | None:
    """Server-owned keys only — OpenAI or Gemini from .env."""
    s = get_settings()
    preferred = (s.llm_provider or "").strip().lower()
    if preferred in {p.id for p in PLATFORM_PROVIDERS}:
        spec = get_provider(preferred)
        if spec:
            key = _settings_key(spec)
            if key:
                return _creds_from_spec(spec, key)
    for spec in PLATFORM_PROVIDERS:
        key = _settings_key(spec)
        if key:
            return _creds_from_spec(spec, key)
    return None


def byok_creds(encrypted_key: str, provider: str = "openai") -> LLMCreds | None:
    key = decrypt_secret(encrypted_key)
    if not key:
        return None
    spec = get_provider(provider) or get_provider("openai")
    assert spec is not None
    return _creds_from_spec(spec, key)


def has_llm(creds: LLMCreds | None = None) -> bool:
    if creds is not None:
        return bool(creds.api_key and creds.provider)
    return platform_creds() is not None


def active_llm_name(creds: LLMCreds | None = None) -> str:
    c = creds or platform_creds()
    return c.provider if c else ""


def _record_request(
    *,
    provider: str,
    model: str,
    purpose: str,
    prompt_version: str,
    status: str,
    latency_ms: int,
    input_chars: int,
    output_chars: int,
    error_type: str | None = None,
) -> None:
    """Persist call metadata without storing prompts, responses, or secrets."""
    try:
        from app.db import SessionLocal
        from app.models import LLMRequest

        with SessionLocal() as db:
            db.add(
                LLMRequest(
                    provider=provider[:64],
                    model=model[:128],
                    purpose=purpose[:64] or "unspecified",
                    prompt_version=prompt_version[:64] or DEFAULT_PROMPT_VERSION,
                    status=status[:32],
                    latency_ms=max(0, latency_ms),
                    input_chars=max(0, input_chars),
                    output_chars=max(0, output_chars),
                    error_type=(error_type or "")[:128] or None,
                )
            )
            db.commit()
    except Exception as exc:
        logger.warning("Could not persist LLM telemetry: %s", exc)


async def llm_complete(
    *,
    prompt: str,
    system: str = "",
    json_mode: bool = False,
    max_tokens: int = 4000,
    temperature: float = 0.3,
    creds: LLMCreds | None = None,
    purpose: str = "unspecified",
    prompt_version: str = DEFAULT_PROMPT_VERSION,
) -> str:
    """Single LLM gateway with safety policy, bounded calls, and privacy-safe telemetry."""
    c = creds or platform_creds()
    if not c:
        raise RuntimeError(
            "No LLM API key configured. Use platform keys or a BYOK key in Settings."
        )

    safe_system = ((system.strip() + "\n\n") if system.strip() else "") + AI_SAFETY_SYSTEM
    started = time.monotonic()
    try:
        if c.kind == "anthropic":
            result = await _anthropic(c, prompt, safe_system, max_tokens, temperature, json_mode)
        elif c.kind == "gemini":
            result = await _gemini(c, prompt, safe_system, json_mode, max_tokens, temperature)
        else:
            result = await _openai_compatible(c, prompt, safe_system, json_mode, temperature)
    except Exception as exc:
        await asyncio.to_thread(
            _record_request,
            provider=c.provider,
            model=c.model,
            purpose=purpose,
            prompt_version=prompt_version,
            status="error",
            latency_ms=int((time.monotonic() - started) * 1000),
            input_chars=len(prompt) + len(safe_system),
            output_chars=0,
            error_type=type(exc).__name__,
        )
        raise

    await asyncio.to_thread(
        _record_request,
        provider=c.provider,
        model=c.model,
        purpose=purpose,
        prompt_version=prompt_version,
        status="success",
        latency_ms=int((time.monotonic() - started) * 1000),
        input_chars=len(prompt) + len(safe_system),
        output_chars=len(result),
    )
    return result


async def _openai_compatible(
    c: LLMCreds, prompt: str, system: str, json_mode: bool, temperature: float
) -> str:
    from openai import AsyncOpenAI

    client_kwargs: dict = {"api_key": c.api_key}
    if c.base_url:
        client_kwargs["base_url"] = c.base_url
    client_kwargs["timeout"] = LLM_TIMEOUT_SECONDS
    client_kwargs["max_retries"] = LLM_MAX_RETRIES
    client = AsyncOpenAI(**client_kwargs)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    kwargs = {
        "model": c.model or c.openai_model or "gpt-4o-mini",
        "temperature": temperature,
        "messages": messages,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = await client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""


async def _anthropic(
    c: LLMCreds,
    prompt: str,
    system: str,
    max_tokens: int,
    temperature: float,
    json_mode: bool = False,
) -> str:
    import anthropic

    client = anthropic.AsyncAnthropic(
        api_key=c.api_key,
        timeout=LLM_TIMEOUT_SECONDS,
        max_retries=LLM_MAX_RETRIES,
    )
    sys = system or ""
    if json_mode:
        sys = (
            (sys + "\n\n") if sys else ""
        ) + "Respond with valid JSON only. Do not wrap the JSON in markdown fences or add prose."
    kwargs = {
        "model": c.model or c.anthropic_model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if sys:
        kwargs["system"] = sys
    resp = await client.messages.create(**kwargs)
    parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
    return "\n".join(parts)


async def _gemini(
    c: LLMCreds, prompt: str, system: str, json_mode: bool, max_tokens: int, temperature: float
) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=c.api_key,
        # HttpOptions timeout is in milliseconds.
        http_options=types.HttpOptions(timeout=int(LLM_TIMEOUT_SECONDS * 1000)),
    )
    config_kwargs: dict = {
        "temperature": temperature,
        "max_output_tokens": max_tokens,
    }
    if system:
        config_kwargs["system_instruction"] = system
    if json_mode:
        config_kwargs["response_mime_type"] = "application/json"

    resp = await client.aio.models.generate_content(
        model=resolve_gemini_model(c.model or c.gemini_model),
        contents=prompt,
        config=types.GenerateContentConfig(**config_kwargs),
    )
    return (resp.text or "").strip()
