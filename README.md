# Vitae (B2C)

Personal job matching: upload your CV, confirm your profile, see roles that match, unlock full access with Bachs.

## Quick start (local)

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt
cp .env.example .env
python run.py
```

Open http://127.0.0.1:8765

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
| `OAUTH_REDIRECT_BASE` | Public HTTPS origin, no trailing slash |
| `BACHS_API_KEY` | Live or sandbox |
| `BACHS_WEBHOOK_SECRET` | Required in Cloud — webhook `…/webhooks/bachs` |
| `BACHS_WEBHOOK_DEV_ACCEPT` | Must be unset/`0` in Cloud |
| Platform LLM keys | At least one of `OPENAI_*` / `ANTHROPIC_*` / `GEMINI_*` for Platform AI |
| Optional | Adzuna / Jooble / OAuth client IDs |

Deploy:

```bash
python -m fastapi deploy
```

Seed a super admin against production DB only via a one-off with `DATABASE_URL` set:

```bash
python -m app.seed --email you@example.com --password '…' --name '…' --role super_admin
```

Then **Admin → Sync now** once (or wait for the hourly catalogue sync).

### Plans (Bachs)

| Plan | NG | Outside NG | Unlocks |
|------|----|------------|---------|
| BYOK | ₦2,000 / mo | $5 / mo | Full job list; AI with your key |
| Platform AI | ₦5,000 / mo | $10 / mo | Full job list; platform LLM |

AI needs subscription status `active` / `trialing`. BYOK also needs a saved LLM key in Settings.

### Admin

Catalogue sync is limited to `admin` / `super_admin` roles (`role` column).
