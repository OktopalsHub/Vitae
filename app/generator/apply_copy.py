"""Apply copy generation — cover letters and application answers.

Orchestrates LLM-powered generation with template fallbacks.
Prompt templates live in apply_prompts.py.
Template fallbacks live in apply_templates.py.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.llm import LLMCreds, has_llm, llm_complete
from app.generator.apply_prompts import APPLY_WRITING_SYSTEM, ASD_STE100_RULES
from app.generator.apply_templates import (
    career_facts,
    experience_highlights,
    full_profile_context,
    jd_analysis,
    job_meta,
    template_application_answers,
    template_cover_blurb,
    template_relevant_experience,
    template_about_yourself,
)

JobLike = Any
logger = logging.getLogger(__name__)

_MIN_LLM_ANSWERS = 5


def _parse_llm_json(text: str) -> Any:
    """Parse JSON from LLM output, handling common LLM quirks."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fixed = re.sub(r",\s*([}\]])", r"\1", text)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    for start_char, end_char in [('{', '}'), ('[', ']')]:
        depth = 0
        in_string = False
        escape = False
        start_idx = None
        for i, ch in enumerate(text):
            if escape:
                escape = False
                continue
            if ch == '\\' and in_string:
                escape = True
                continue
            if ch == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == start_char:
                if depth == 0:
                    start_idx = i
                depth += 1
            elif ch == end_char:
                depth -= 1
                if depth == 0 and start_idx is not None:
                    try:
                        return json.loads(text[start_idx:i + 1])
                    except json.JSONDecodeError:
                        break
        break
    raise json.JSONDecodeError("Unable to extract valid JSON from LLM output", text, 0)


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

async def _llm_text(
    prompt: str,
    fallback: str,
    max_tokens: int = 1200,
    creds: LLMCreds | None = None,
    *,
    system: str = "",
    temperature: float = 0.4,
) -> str:
    if not has_llm(creds):
        return fallback
    try:
        text = await llm_complete(
            prompt=prompt,
            system=system or APPLY_WRITING_SYSTEM,
            json_mode=False,
            max_tokens=max_tokens,
            temperature=temperature,
            creds=creds,
        )
        return text or fallback
    except Exception as exc:
        logger.warning("LLM call failed: %s", exc)
        return fallback


def _strip_timeline_labels(text: str) -> str:
    cleaned = re.sub(r"(?im)^\s*(present|past|future)\s*:\s*", "", text or "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


# ---------------------------------------------------------------------------
# LLM prompt builders
# ---------------------------------------------------------------------------

async def generate_cover_blurb(
    job: JobLike,
    apply_profile: dict[str, Any],
    *,
    previous: str = "",
    rewrite: bool = False,
    creds: LLMCreds | None = None,
) -> str:
    fallback = template_cover_blurb(job, apply_profile)
    if not has_llm(creds):
        return fallback
    rewrite_bit = ""
    if rewrite and previous.strip():
        rewrite_bit = (
            "The user wants a rewrite. The previous version:\n"
            f"---\n{previous[:2000]}\n---\n"
            "Write a new cover note. Do not reuse sentences from the previous version.\n\n"
        )
    prompt = (
        f"Write a short cover note (90-140 words) for a specific job application.\n\n"
        f"{ASD_STE100_RULES}\n\n"
        "STRUCTURE (ASD-STE100):\n"
        "1) Who you are. One sentence. Name + years + one fact from the profile.\n"
        "2) What you have done. One or two sentences. One concrete fact from the profile.\n"
        "3) Why this company. One sentence. Reference something from the JD.\n"
        "4) Close. One sentence. Resume attached, available to discuss.\n\n"
        "RULES:\n"
        "- Max 20 words per sentence. One idea per sentence.\n"
        "- Active voice. Start with the subject.\n"
        "- Every claim MUST come from the profile below.\n"
        "- Do NOT use: passionate, results-driven, world-class, cutting-edge, seamless.\n"
        "- Do NOT write: 'I want the role because the JD focuses on...'\n"
        "- Name the company once. Do not repeat it in every sentence.\n"
        "- Do NOT start sentences with 'I am writing' or 'I am interested'. Start with who you are or what you have done.\n"
        "- Make the first sentence memorable. Lead with your strongest fact.\n"
        "- Connect your experience to what the company actually builds or does.\n\n"
        f"{rewrite_bit}"
        f"{full_profile_context(apply_profile)}\n\n"
        f"{job_meta(job)}\n"
        f"{jd_analysis(job)}\n\n"
        "Write the cover note now. Return plain text only."
    )
    return await _llm_text(prompt, fallback, max_tokens=550, creds=creds, temperature=0.5)


async def generate_application_answers(
    job: JobLike,
    apply_profile: dict[str, Any],
    *,
    rewrite: bool = False,
    creds: LLMCreds | None = None,
) -> list[dict[str, str]]:
    fallback = template_application_answers(job, apply_profile)
    if not has_llm(creds):
        return fallback
    rewrite_bit = (
        "The user wants a complete rewrite. Produce fresh answers. "
        "Use different evidence and angles. No stock phrases.\n\n"
        if rewrite
        else ""
    )
    prompt = (
        "Write application-form answers for a specific job.\n"
        "Return ONLY valid JSON: {\"answers\":[{\"question\":\"...\",\"answer\":\"...\"}]}\n\n"
        f"{ASD_STE100_RULES}\n\n"
        f"{rewrite_bit}"
        "ANSWER RULES:\n"
        "- Every claim MUST come from the candidate profile below.\n"
        "- Every answer MUST be specific to this JD.\n"
        "- Max 20 words per sentence. One idea per sentence. Active voice.\n"
        "- Do NOT use: passionate, results-driven, world-class, seamless.\n"
        "- Do NOT write: 'the JD focuses on...', 'matches work I already do'.\n"
        "- Write like a real person, not a template. Use natural phrasing.\n"
        "- Each answer should feel like it was written for THIS specific job, not copied from a generic script.\n\n"
        "QUESTIONS:\n\n"
        "1. Tell us about yourself (110-170 words)\n"
        "   Use this structure:\n"
        "   PAST: What you have done. One or two facts from the profile.\n"
        "   PRESENT: What you do now. Your current focus or role.\n"
        "   FUTURE: Why this company. One sentence connecting to the JD.\n"
        "   Write it as natural prose, not labelled sections.\n"
        "   Do NOT start with 'I am a [title] with X years of experience'. Start with something specific you have built or done.\n\n"
        "2. Why do you want this role / Why this company? (80-130 words)\n"
        "   Name something specific from the JD (product, users, problem).\n"
        "   Tie it to one real experience from the profile.\n"
        "   Do NOT say 'I am excited about' or 'I am passionate about'. Say what specifically draws you to this work.\n\n"
        "3. Relevant experience / What makes you a fit? (110-170 words)\n"
        "   Map 2-3 JD needs to profile experience (employer + outcome).\n"
        "   Must NOT be generic. Should only work for THIS company.\n"
        "   Use specific numbers, tools, and outcomes from the profile.\n\n"
        "4. Years of experience\n"
        "5. Biggest achievement\n"
        "6. Work authorization / Location\n"
        "7. Salary expectation\n"
        "8. Earliest start date\n"
        "9. Notice period / Availability\n\n"
        "Short answers (4-9) stay factual from the profile.\n\n"
        f"{full_profile_context(apply_profile)}\n\n"
        f"{job_meta(job)}\n"
        f"{jd_analysis(job)}\n"
    )
    raw = await _llm_text(prompt, "", max_tokens=4000, creds=creds, temperature=0.5)
    if not raw:
        return fallback
    try:
        data = _parse_llm_json(raw)
        answers = data.get("answers") if isinstance(data, dict) else data
        cleaned: list[dict[str, str]] = []
        if isinstance(answers, list):
            for item in answers:
                if not isinstance(item, dict):
                    continue
                q = str(item.get("question") or "").strip()
                a = str(item.get("answer") or "").strip()
                if q and a:
                    if "about yourself" in q.lower():
                        a = _strip_timeline_labels(a)
                    cleaned.append({"question": q, "answer": a})
        if len(cleaned) < _MIN_LLM_ANSWERS:
            logger.warning(
                "LLM returned only %s application answers (need %s); using template fallback",
                len(cleaned),
                _MIN_LLM_ANSWERS,
            )
            return fallback
        return cleaned
    except Exception as exc:
        logger.warning("Failed to parse LLM application answers; using fallback: %s", exc)
        return fallback


async def rewrite_single_answer(
    job: JobLike,
    apply_profile: dict[str, Any],
    question: str,
    previous_answer: str = "",
    creds: LLMCreds | None = None,
) -> str:
    question = (question or "").strip()
    previous_answer = (previous_answer or "").strip()
    fallbacks = {
        item["question"].lower(): item["answer"]
        for item in template_application_answers(job, apply_profile)
    }
    fallback = previous_answer or fallbacks.get(question.lower()) or template_about_yourself(job, apply_profile)
    ql = question.lower()

    focus = (
        "Rewrite this answer in ASD-STE100 voice.\n"
        "Max 20 words per sentence. One idea per sentence. Active voice.\n"
        "Every claim must trace to the profile. Must be specific to THIS JD.\n"
        "Do NOT use: passionate, results-driven, world-class, seamless, robust.\n"
        "Write like a real person. Avoid template phrases.\n"
    )
    if "about yourself" in ql:
        focus = (
            "Rewrite 'Tell me about yourself' in ASD-STE100 voice.\n"
            "Use this structure (as natural prose, not labelled):\n"
            "PAST: What you have done. One or two facts from the profile.\n"
            "PRESENT: What you do now. Your current focus or role.\n"
            "FUTURE: Why this company. One sentence connecting to the JD.\n"
            "Max 20 words per sentence. Active voice. No hype words.\n"
            "Do NOT start with 'I am a [title] with X years of experience'. Start with something specific you have built.\n"
        )
    elif any(k in ql for k in ("why this", "why do you want", "why our", "company")):
        focus = (
            "Rewrite 'Why this role' in ASD-STE100 voice.\n"
            "Name something specific from the JD (product, users, problem).\n"
            "Tie it to one real experience from the profile.\n"
            "Forbidden: 'JD focuses on', 'matches work I already do'.\n"
            "Do NOT say 'I am excited about'. Say what specifically draws you to this work.\n"
        )
    elif any(k in ql for k in ("relevant experience", "makes you a fit", "why are you a fit", "why you")):
        focus = (
            "Rewrite 'Relevant experience' in ASD-STE100 voice.\n"
            "Map 2-3 JD needs to profile experience (employer + outcome).\n"
            "Must NOT be generic. Should only work for THIS company.\n"
            "Use specific numbers, tools, and outcomes from the profile.\n"
        )
    prompt = (
        f"{focus}\n"
        f"Question: {question}\n\n"
        f"Previous answer (improve this — do not paraphrase fluff):\n"
        f"---\n{previous_answer[:2500]}\n---\n\n"
        f"{full_profile_context(apply_profile)}\n\n"
        f"{job_meta(job)}\n"
        f"{jd_analysis(job)}\n\n"
        "Return plain text only."
    )
    text = await _llm_text(prompt, fallback, max_tokens=800, creds=creds)
    if "about yourself" in ql:
        return _strip_timeline_labels(text)
    return text
