"""Canonical LLM provider registry.

Platform (.env) only uses OpenAI + Gemini.
BYOK Settings lets users pick from the full list (encrypted per account).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    placeholder: str
    hint: str
    logo: str
    kind: str  # openai | anthropic | gemini | openai_compat
    default_model: str
    base_url: str = ""
    # Server .env fields — only set when for_platform=True
    env_key_attr: str = ""
    env_model_attr: str = ""
    for_platform: bool = False


# Full BYOK list (Settings UI). Platform subset marked for_platform=True.
PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        id="openai",
        label="OpenAI",
        placeholder="sk-…",
        hint="GPT-4o mini and other chat models",
        logo="/static/logos/openai.svg",
        kind="openai",
        default_model="gpt-4o-mini",
        env_key_attr="openai_api_key",
        env_model_attr="openai_model",
        for_platform=True,
    ),
    ProviderSpec(
        id="anthropic",
        label="Anthropic",
        placeholder="sk-ant-…",
        hint="Claude models",
        logo="/static/logos/anthropic.svg",
        kind="anthropic",
        default_model="claude-3-5-haiku-latest",
    ),
    ProviderSpec(
        id="gemini",
        label="Google Gemini",
        placeholder="AIza…",
        hint="Gemini flash / pro",
        logo="/static/logos/gemini.svg",
        kind="gemini",
        default_model="gemini-2.0-flash",
        env_key_attr="gemini_api_key",
        env_model_attr="gemini_model",
        for_platform=True,
    ),
    ProviderSpec(
        id="deepseek",
        label="DeepSeek",
        placeholder="sk-…",
        hint="DeepSeek chat (OpenAI-compatible)",
        logo="/static/logos/deepseek.svg",
        kind="openai_compat",
        default_model="deepseek-chat",
        base_url="https://api.deepseek.com",
    ),
    ProviderSpec(
        id="glm",
        label="GLM (Zhipu)",
        placeholder="…",
        hint="Zhipu GLM via OpenAI-compatible API",
        logo="/static/logos/glm.svg",
        kind="openai_compat",
        default_model="glm-4-flash",
        base_url="https://open.bigmodel.cn/api/paas/v4",
    ),
    ProviderSpec(
        id="grok",
        label="Grok (xAI)",
        placeholder="xai-…",
        hint="xAI Grok (OpenAI-compatible)",
        logo="/static/logos/grok.svg",
        kind="openai_compat",
        default_model="grok-2-latest",
        base_url="https://api.x.ai/v1",
    ),
    ProviderSpec(
        id="mistral",
        label="Mistral",
        placeholder="…",
        hint="Mistral chat (OpenAI-compatible)",
        logo="/static/logos/mistral.svg",
        kind="openai_compat",
        default_model="mistral-small-latest",
        base_url="https://api.mistral.ai/v1",
    ),
    ProviderSpec(
        id="openrouter",
        label="OpenRouter",
        placeholder="sk-or-…",
        hint="Route to many models with one key",
        logo="/static/logos/openrouter.svg",
        kind="openai_compat",
        default_model="openai/gpt-4o-mini",
        base_url="https://openrouter.ai/api/v1",
    ),
    ProviderSpec(
        id="together",
        label="Together AI",
        placeholder="…",
        hint="Open models via Together (OpenAI-compatible)",
        logo="/static/logos/together.svg",
        kind="openai_compat",
        default_model="meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
        base_url="https://api.together.xyz/v1",
    ),
)

PROVIDER_BY_ID: dict[str, ProviderSpec] = {p.id: p for p in PROVIDERS}
LLM_PROVIDERS: tuple[str, ...] = tuple(p.id for p in PROVIDERS)
PLATFORM_PROVIDERS: tuple[ProviderSpec, ...] = tuple(p for p in PROVIDERS if p.for_platform)
PLATFORM_PROVIDER_IDS: frozenset[str] = frozenset(p.id for p in PLATFORM_PROVIDERS)


def get_provider(provider_id: str) -> ProviderSpec | None:
    return PROVIDER_BY_ID.get((provider_id or "").strip().lower())
