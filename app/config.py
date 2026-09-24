from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic_settings import BaseSettings

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


class Settings(BaseSettings):
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    jooble_api_key: str = ""
    # Optional open-web discovery via Firecrawl. Disabled unless explicitly enabled in config.yaml.
    firecrawl_api_key: str = ""
    # Optional shared Redis backend for rate limiting across web replicas.
    redis_url: str = ""
    rate_limit_backend: str = "local"
    forwarded_allow_ips: str = "127.0.0.1"
    metrics_token: str = ""
    # Generated CV/object storage. Use s3 in production for durable artifacts.
    object_storage_backend: str = "local"
    object_storage_bucket: str = ""
    object_storage_region: str = ""
    object_storage_endpoint: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    # Platform only: openai | gemini (BYOK users pick their own provider in Settings)
    llm_provider: str = ""
    app_host: str = "127.0.0.1"
    app_port: int = 8765
    app_env: str = "development"
    database_url: str = "sqlite:///./data/jobs.db"
    # No default — must be set in .env.  Startup will raise if empty in non-test envs.
    secret_key: str = ""
    oauth_redirect_base: str = "http://127.0.0.1:8765"
    # Public site URL for canonical tags, sitemap, llms.txt (defaults to oauth_redirect_base).
    site_url: str = ""
    # Google Search Console HTML-tag verification content (meta content value only).
    google_site_verification: str = ""
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    github_oauth_client_id: str = ""
    github_oauth_client_secret: str = ""
    bachs_api_key: str = ""
    bachs_api_base: str = ""
    bachs_webhook_secret: str = ""
    # Four Bachs monthly products: NG = NGN ₦2k/₦5k · intl = USD $5/$10
    bachs_product_byok_ng: str = ""
    bachs_product_platform_ng: str = ""
    bachs_product_byok_intl: str = ""
    bachs_product_platform_intl: str = ""
    # Default on for staging/Cloud (CF / Vercel country → NG vs intl products).
    # Set TRUST_EDGE_GEO=0 locally to ignore country headers (spoofable without a real edge).
    trust_edge_geo: bool = True
    max_upload_bytes: int = 8 * 1024 * 1024
    # Separate from SECRET_KEY so JWT rotation does not invalidate BYOK ciphertext.
    fernet_secret_key: str = ""

    model_config = {"env_file": str(ROOT_DIR / ".env"), "extra": "ignore"}


# Legacy sentinel kept only so external callers that import this name don't break.
# Never use it as an actual default — it is a known-public string.
DEFAULT_SECRET_KEY = "dev-insecure-secret-change-me-32b+"

_TEST_ENVS = {"test", "testing"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _is_test_env() -> bool:
    s = get_settings()
    return (s.app_env or "").strip().lower() in _TEST_ENVS


def _is_prod_like() -> bool:
    return (get_settings().app_env or "").strip().lower() in {"production", "prod", "cloud"}


def assert_secure_settings() -> None:
    """Fail fast if SECRET_KEY or FERNET_SECRET_KEY are missing / insecure.

    In test environments (APP_ENV=test) missing keys are allowed so unit tests
    can run without secrets.  All other environments — including development,
    staging, and production — require both keys to be explicitly set.
    """
    if _is_test_env():
        return
    s = get_settings()
    key = (s.secret_key or "").strip()
    if not key or key == DEFAULT_SECRET_KEY:
        raise RuntimeError(
            "SECRET_KEY must be set to a strong, unique value in .env. "
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    fernet = (s.fernet_secret_key or "").strip()
    if not fernet:
        raise RuntimeError(
            "FERNET_SECRET_KEY must be set to a strong, unique value in .env. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    if _is_prod_like():
        metrics_token = (s.metrics_token or "").strip()
        if metrics_token and len(metrics_token) < 32:
            raise RuntimeError("METRICS_TOKEN must be at least 32 characters when configured.")

        rate_backend = (s.rate_limit_backend or "local").strip().lower()
        if rate_backend == "redis" and not (s.redis_url or "").strip():
            raise RuntimeError("REDIS_URL is required when RATE_LIMIT_BACKEND=redis.")
        if rate_backend not in {"local", "redis"}:
            raise RuntimeError("RATE_LIMIT_BACKEND must be local or redis.")

        storage_backend = (s.object_storage_backend or "local").strip().lower()
        if storage_backend == "s3":
            if not (s.object_storage_bucket or "").strip():
                raise RuntimeError("OBJECT_STORAGE_BUCKET is required when OBJECT_STORAGE_BACKEND=s3.")
            endpoint = (s.object_storage_endpoint or "").strip()
            if endpoint and not endpoint.startswith("https://"):
                raise RuntimeError("OBJECT_STORAGE_ENDPOINT must use HTTPS in production.")
            if endpoint and "r2.cloudflarestorage.com" in endpoint:
                if not os.getenv("AWS_ACCESS_KEY_ID") or not os.getenv("AWS_SECRET_ACCESS_KEY"):
                    raise RuntimeError("Cloudflare R2 requires AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in production.")
        elif storage_backend != "local":
            raise RuntimeError("OBJECT_STORAGE_BACKEND must be local or s3.")



def warn_site_settings() -> None:
    """Log non-fatal reminders for SEO/trust config in production."""
    import logging

    if _is_test_env():
        return
    s = get_settings()
    logger = logging.getLogger("app.config")
    if _is_prod_like():
        if not (s.google_site_verification or "").strip():
            logger.warning(
                "GOOGLE_SITE_VERIFICATION is unset — Search Console HTML-tag verification "
                "will not work until you set the content value from the meta tag."
            )
        else:
            base = site_base_url()
            logger.info(
                "Google Search Console: verify property at %s, then submit sitemap %s/sitemap.xml",
                base,
                base,
            )


@lru_cache
def load_yaml_config() -> dict[str, Any]:
    path = ROOT_DIR / "config.yaml"
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def reload_yaml_config() -> dict[str, Any]:
    load_yaml_config.cache_clear()
    return load_yaml_config()


def project_path(*parts: str) -> Path:
    return ROOT_DIR.joinpath(*parts)


def ensure_dirs() -> None:
    (ROOT_DIR / "data").mkdir(exist_ok=True)
    (ROOT_DIR / "data" / "users").mkdir(parents=True, exist_ok=True)


def database_url() -> str:
    settings = get_settings()
    url = (settings.database_url or "").strip()
    if not url:
        url = "sqlite:///./data/jobs.db"
    # Normalize postgres URLs for SQLAlchemy + psycopg3
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgres://")
    elif url.startswith("postgresql://") and "+psycopg" not in url:
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    if url.startswith("sqlite:///./"):
        rel = url.removeprefix("sqlite:///./")
        abs_path = (ROOT_DIR / rel).resolve()
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{abs_path.as_posix()}"
    return url


def uses_sqlite() -> bool:
    return database_url().startswith("sqlite")


def site_base_url() -> str:
    """Canonical public origin (no trailing slash)."""
    s = get_settings()
    base = (s.site_url or s.oauth_redirect_base or "").strip().rstrip("/")
    return base or "http://127.0.0.1:8765"


def api_key_status() -> dict[str, bool]:
    from app.llm_providers import PLATFORM_PROVIDERS

    s = get_settings()
    status: dict[str, bool] = {
        "adzuna": bool(s.adzuna_app_id and s.adzuna_app_key),
        "jooble": bool(s.jooble_api_key),
        "firecrawl": bool(s.firecrawl_api_key),
    }
    any_llm = False
    for spec in PLATFORM_PROVIDERS:
        configured = bool((getattr(s, spec.env_key_attr, "") or "").strip())
        status[spec.id] = configured
        any_llm = any_llm or configured
    status["llm"] = any_llm
    return status
