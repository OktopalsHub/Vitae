from __future__ import annotations

from dataclasses import dataclass

from app.config import get_settings
from app.crypto import decrypt_secret
from app.llm_providers import PLATFORM_PROVIDERS, ProviderSpec, get_provider


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
        if self.kind == "gemini" and not self.gemini_model:
            self.gemini_model = self.model


def _settings_key(spec: ProviderSpec) -> str:
    if not spec.env_key_attr:
        return ""
    s = get_settings()
    return (getattr(s, spec.env_key_attr, "") or "").strip()


def _model_for_spec(spec: ProviderSpec) -> str:
    """Platform OpenAI/Gemini can override model via .env; BYOK uses registry defaults."""
    if spec.env_model_attr:
        s = get_settings()
        override = (getattr(s, spec.env_model_attr, "") or "").strip()
        if override:
            return override
    return spec.default_model


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


async def llm_complete(
    *,
    prompt: str,
    system: str = "",
    json_mode: bool = False,
    max_tokens: int = 4000,
    temperature: float = 0.3,
    creds: LLMCreds | None = None,
) -> str:
    """Call the configured LLM. Raises RuntimeError if no key is set."""
    c = creds or platform_creds()
    if not c:
        raise RuntimeError(
            "No LLM API key configured. Use platform keys or a BYOK key in Settings."
        )

    if c.kind == "anthropic":
        return await _anthropic(c, prompt, system, max_tokens, temperature)
    if c.kind == "gemini":
        return await _gemini(c, prompt, system, json_mode, max_tokens, temperature)
    return await _openai_compatible(c, prompt, system, json_mode, temperature)


async def _openai_compatible(
    c: LLMCreds, prompt: str, system: str, json_mode: bool, temperature: float
) -> str:
    from openai import AsyncOpenAI

    client_kwargs: dict = {"api_key": c.api_key}
    if c.base_url:
        client_kwargs["base_url"] = c.base_url
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
    c: LLMCreds, prompt: str, system: str, max_tokens: int, temperature: float
) -> str:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=c.api_key)
    kwargs = {
        "model": c.model or c.anthropic_model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    resp = await client.messages.create(**kwargs)
    parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
    return "\n".join(parts)


async def _gemini(
    c: LLMCreds, prompt: str, system: str, json_mode: bool, max_tokens: int, temperature: float
) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=c.api_key)
    config_kwargs: dict = {
        "temperature": temperature,
        "max_output_tokens": max_tokens,
    }
    if system:
        config_kwargs["system_instruction"] = system
    if json_mode:
        config_kwargs["response_mime_type"] = "application/json"

    resp = await client.aio.models.generate_content(
        model=c.model or c.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(**config_kwargs),
    )
    return (resp.text or "").strip()
