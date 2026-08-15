"""generator package — consolidated AI generation for Vitae.

All prompt engineering and content generation lives here:
  - apply_prompts.py  — ASD-STE100 rules, system prompt, JD wrapper
  - apply_templates.py — template fallbacks when no LLM available
  - apply_copy.py     — LLM-powered cover letters, application answers
  - cv_prompts.py     — resume tailoring system prompt
  - cv_helpers.py      — text helpers, rule-based fallback, post-validation
  - cv_export.py      — DOCX, PDF, Markdown writers
  - cv_tailor.py      — resume tailoring orchestration
  - orchestrate.py    — coordination layer, stale copy detection
"""

from __future__ import annotations

from app.generator.orchestrate import (
    apply_assist_payload,
    default_apply_profile,
    ensure_apply_copy,
    load_apply_profile,
    rewrite_answer_in_draft,
)
from app.generator.apply_prompts import APPLY_WRITING_SYSTEM
from app.generator.apply_copy import (
    generate_application_answers,
    generate_cover_blurb,
    rewrite_single_answer,
)
from app.generator.apply_templates import (
    template_application_answers,
    template_about_yourself,
    template_cover_blurb,
    template_relevant_experience,
)
from app.generator.cv_tailor import (
    build_tailored_content,
    generate_resume_files,
    list_resume_files,
    output_dir_for,
    resume_basename,
    resume_to_markdown,
    write_docx,
    write_pdf,
    assert_download_under_user,
)

ensure_user_apply_copy = ensure_apply_copy

__all__ = [
    # orchestration
    "apply_assist_payload",
    "default_apply_profile",
    "ensure_apply_copy",
    "ensure_user_apply_copy",
    "load_apply_profile",
    "rewrite_answer_in_draft",
    # apply copy prompts
    "APPLY_WRITING_SYSTEM",
    "generate_application_answers",
    "generate_cover_blurb",
    "rewrite_single_answer",
    "template_application_answers",
    "template_about_yourself",
    "template_cover_blurb",
    "template_relevant_experience",
    # cv tailor
    "build_tailored_content",
    "generate_resume_files",
    "list_resume_files",
    "output_dir_for",
    "resume_basename",
    "resume_to_markdown",
    "write_docx",
    "write_pdf",
    "assert_download_under_user",
]
