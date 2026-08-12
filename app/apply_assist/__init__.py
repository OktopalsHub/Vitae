"""apply_assist package — backward-compatible re-exports.

The module has been split into:
  prompts.py      — LLM prompt builders (generate_cover_blurb, etc.)
  payload.py      — apply_assist_payload, ensure_user_apply_copy
  legacy_draft.py — deprecated file-based draft I/O

Import from the sub-modules directly in new code.
This __init__ re-exports the public API so existing
  from app.apply_assist import X
call-sites continue to work.
"""

from __future__ import annotations

from app.apply_assist.legacy_draft import (
    APPLY_PROFILE_PATH,
    DRAFTS_DIR,
    load_job_draft,
    save_job_draft,
)
from app.apply_assist.payload import (
    apply_assist_payload,
    default_apply_profile,
    ensure_apply_copy,
    load_apply_profile,
    rewrite_answer_in_draft,
)
from app.apply_assist.prompts import (
    APPLY_WRITING_SYSTEM,
    generate_application_answers,
    generate_cover_blurb,
    rewrite_single_answer,
    template_application_answers,
    template_about_yourself,
    template_cover_blurb,
    template_relevant_experience,
)

# ensure_user_apply_copy is the DB-backed alias used in routes
ensure_user_apply_copy = ensure_apply_copy

__all__ = [
    # legacy draft
    "APPLY_PROFILE_PATH",
    "DRAFTS_DIR",
    "load_job_draft",
    "save_job_draft",
    # payload / copy
    "apply_assist_payload",
    "default_apply_profile",
    "ensure_apply_copy",
    "ensure_user_apply_copy",
    "load_apply_profile",
    "rewrite_answer_in_draft",
    # prompts
    "APPLY_WRITING_SYSTEM",
    "generate_application_answers",
    "generate_cover_blurb",
    "rewrite_single_answer",
    "template_application_answers",
    "template_about_yourself",
    "template_cover_blurb",
    "template_relevant_experience",
]
