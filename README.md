# Vitae (B2C)

Personal job matching: upload your CV, confirm your profile, see roles that match, unlock full access with Bachs.

## Quick start (local)

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
# Install uv if needed: https://docs.astral.sh/uv/getting-started/installation/
uv sync
cp .env.example .env   # or copy from .env.local
uv run python run.py
```

Open http://127.0.0.1:8765

`uv sync` creates/manages `.venv` for you — you do not need `python -m venv` or `pip install`.

Local `.env` can use SQLite (`DATABASE_URL=sqlite:///./data/jobs.db`) and the default `SECRET_KEY`.  
Each user uploads their own CV — do not commit personal DOCX files.

### Flow

1. **Register / sign in**
2. **Upload CV** (PDF or DOCX) — compulsory
3. **Confirm sections** — editable later in Settings
4. **Billing** (after onboarding) — choose a plan or **Skip**
5. **Matches** (≥ default **65%**) — free/basic sees **3** clear roles; the rest are locked until paid
6. **AI** (rewrites / tailored CV) needs an active paid plan

### Background jobs (while the app is running)

| Job | Interval | Purpose |
|-----|----------|---------|
| Catalogue sync | 1 hour | Fetch/upsert/close public `job_listings` |
| User rank refresh | 3 hours | Rescore existing `user_jobs` overlays only |

### FastAPI Cloud (production)

Set these in Cloud env (never commit secrets):

| Variable | Notes |
|----------|--------|
| `APP_ENV` | `production` or `cloud` |
| `APP_HOST` | `0.0.0.0` |
| `DATABASE_URL` | Postgres (Neon). Not SQLite. |
| `SECRET_KEY` | Strong unique secret (required — app refuses the default) |
| `FERNET_SECRET_KEY` | Recommended separate secret for BYOK key encryption |
| `OAUTH_REDIRECT_BASE` | Public HTTPS origin, no trailing slash |
| `BACHS_API_KEY` | Live or sandbox |
| `BACHS_WEBHOOK_SECRET` | Required in Cloud — webhook `…/webhooks/bachs` |
| `BACHS_WEBHOOK_DEV_ACCEPT` | Must be unset/`0` in Cloud |
| Platform LLM keys | At least one of `OPENAI_*` / `ANTHROPIC_*` / `GEMINI_*` for Platform AI |
| Optional | Adzuna / Jooble / OAuth client IDs |

Deploy:

```bash
uv run python -m fastapi deploy
```

Seed a super admin against production DB only via a one-off with `DATABASE_URL` set:

```bash
uv run python -m app.seed --email you@example.com --password '…' --name '…' --role super_admin
```

Then **Admin → Sync now** once (or wait for the hourly catalogue sync).

### Plans (Bachs)

Billing is **per profile**. Use **four** sandbox recurring products:

| Plan | Nigeria (NGN) | International (USD) | Unlocks |
|------|---------------|---------------------|---------|
| BYOK | ₦2,000 / mo | $5 / mo | Full job list; AI with your key |
| Platform AI | ₦5,000 / mo | $10 / mo | Full job list; platform LLM |

Set `BACHS_PRODUCT_BYOK_NG`, `BACHS_PRODUCT_PLATFORM_NG`, `BACHS_PRODUCT_BYOK_INTL`, `BACHS_PRODUCT_PLATFORM_INTL`. Enable `TRUST_EDGE_GEO=1` in Cloud so NG visitors get the Naira products.

AI needs subscription status `active` / `trialing`. BYOK also needs a saved LLM key in Settings.

### Admin

Catalogue sync is limited to `admin` / `super_admin` roles (`role` column).

### Tests

```bash
uv sync
uv run pytest
```

Critical-path coverage: auth privilege stripping, Bachs webhook HMAC + identity binding, upload magic-byte validation, CV generation fallback, and apply-copy templates.
