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
    """Server-owned keys only — OpenAI, Gemini or Groq from .env."""
    chain = platform_creds_chain()
    return chain[0] if chain else None


def platform_creds_chain() -> list[LLMCreds]:
    """Ordered platform providers to try: preferred first, then fallbacks.

    The first entry honours LLM_PROVIDER when set, then the remaining
    configured platform keys, then LLM_FALLBACK_ORDER (e.g. "openai,groq") so
    a rate-limited or outage provider does not take AI features down with it.
    Each provider appears at most once.
    """
    s = get_settings()
    ordered: list[ProviderSpec] = []
    seen: set[str] = set()

    def _add(spec: ProviderSpec | None) -> None:
        if spec is None or spec.id in seen or not spec.env_key_attr:
            return
        if not _settings_key(spec):
            return
        seen.add(spec.id)
        ordered.append(spec)

    preferred = (s.llm_provider or "").strip().lower()
    if preferred:
        _add(get_provider(preferred))

    # Any other configured platform key, before the explicit fallback order.
    for spec in PLATFORM_PROVIDERS:
        _add(spec)

    for name in (s.llm_fallback_order or "").split(","):
        _add(get_provider(name.strip().lower()))

    return [_creds_from_spec(spec, _settings_key(spec)) for spec in ordered]


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


async def _dispatch(
    c: LLMCreds, prompt: str, system: str, json_mode: bool, max_tokens: int, temperature: float
) -> str:
    """Route one call to the client that matches this provider's API shape."""
    if c.kind == "anthropic":
        return await _anthropic(c, prompt, system, max_tokens, temperature, json_mode)
    if c.kind == "gemini":
        return await _gemini(c, prompt, system, json_mode, max_tokens, temperature)
    return await _openai_compatible(c, prompt, system, json_mode, temperature)


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
    allow_fallback: bool = True,
) -> str:
    """Single LLM gateway with safety policy, bounded calls, and privacy-safe telemetry.

    When no explicit creds are supplied, platform keys are tried in order and a
    provider failure (outage, rate limit, bad key) transparently falls through to
    the next one. Set allow_fallback=False to fail on the first error instead.
    Each attempt is recorded separately so fallbacks stay visible in telemetry.
    """
    if creds is not None:
        chain = [creds]
    else:
        chain = platform_creds_chain()
    if not chain:
        raise RuntimeError(
            "No LLM API key configured. Use platform keys or a BYOK key in Settings."
        )

    safe_system = ((system.strip() + "\n\n") if system.strip() else "") + AI_SAFETY_SYSTEM
    input_chars = len(prompt) + len(safe_system)
    last_exc: Exception | None = None

    for index, c in enumerate(chain):
        is_last = index == len(chain) - 1
        started = time.monotonic()
        try:
            result = await _dispatch(
                c, prompt, safe_system, json_mode, max_tokens, temperature
            )
        except Exception as exc:
            last_exc = exc
            await asyncio.to_thread(
                _record_request,
                provider=c.provider,
                model=c.model,
                purpose=purpose,
                prompt_version=prompt_version,
                status="error",
                latency_ms=int((time.monotonic() - started) * 1000),
                input_chars=input_chars,
                output_chars=0,
                error_type=type(exc).__name__,
            )
            # Only fall through when another provider is left to try.
            if is_last or not allow_fallback:
                raise
            nxt = chain[index + 1]
            logger.warning(
                "LLM provider %s failed (%s: %s); falling back to %s",
                c.provider,
                type(exc).__name__,
                exc,
                nxt.provider,
            )
            continue

        await asyncio.to_thread(
            _record_request,
            provider=c.provider,
            model=c.model,
            purpose=purpose,
            prompt_version=prompt_version,
            status="success",
            latency_ms=int((time.monotonic() - started) * 1000),
            input_chars=input_chars,
            output_chars=len(result),
        )
        return result

    # Unreachable: the loop either returns or raises.
    raise last_exc or RuntimeError("LLM call failed with no provider available")


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
