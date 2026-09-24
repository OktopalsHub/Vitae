from __future__ import annotations

import re
from math import ceil
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.generator import apply_assist_payload, rewrite_answer_in_draft
from app.config import project_path
from app.db import get_db
from app.matching.scorer import reasons_from_json, score_job_detail
from app.models import JobListing, JobStatus, ListingVisibility, User
from app.services import (
    add_pasted_job_for_user,
    get_user_job_card_by_public_id,
    job_path,
    list_job_cards,
    rescore_user_jobs,
    sync_public_jobs,
    search_job_cards,
)
from app.generator import generate_resume_files, list_resume_files
from app.accounts import (
    apply_profile_from_user,
    can_open_listing,
    can_use_ai,
    detect_billing_region_detail,
    free_opens_remaining,
    free_unlocked_listing_ids,
    get_active_profile,
    has_full_job_access,
    llm_creds_for_user,
    load_apply_draft_db,
    load_user_profile_dict,
    load_user_settings,
    save_apply_draft_db,
    FREE_CLEAR_MATCHES,
)
from app.rate_limit import enforce
from app.workers.queue import enqueue_job
from app.web_helpers import (
    assert_download_under_user,
    ensure_user_apply_copy,
    flash_redirect,
    require_profile_ready,
    require_user,
    safe_http_url,
    template_ctx,
)
from app.roles import is_admin

router = APIRouter(tags=["jobs"])
templates = Jinja2Templates(directory=str(project_path("app", "templates")))
templates.env.filters["safe_http_url"] = safe_http_url
templates.env.filters["path_quote"] = lambda value: quote(str(value), safe="")
templates.env.filters["match_reasons"] = reasons_from_json

JOBS_PAGE_SIZE = 20
_SAFE_JOBS_QS = re.compile(r"^[A-Za-z0-9._=&%+\-]*$")


def _jobs_list_path(from_qs: str = "") -> str:
    """Same-tab return to the filtered jobs board (no open redirects)."""
    qs = (from_qs or "").lstrip("?")
    if not qs or qs.startswith("/") or not _SAFE_JOBS_QS.match(qs):
        return "/jobs"
    return f"/jobs?{qs}"


def _jobs_query_string(
    *,
    status: str,
    min_score: float,
    q: str,
    page: int | None = None,
    location: str = "",
    remote_type: str = "",
    employment_type: str = "",
    experience_level: str = "",
) -> str:
    params: dict[str, str | float | int] = {"min_score": int(min_score) if float(min_score).is_integer() else min_score}
    if status: params["status"] = status
    if q: params["q"] = q
    if location: params["location"] = location
    if remote_type: params["remote_type"] = remote_type
    if employment_type: params["employment_type"] = employment_type
    if experience_level: params["experience_level"] = experience_level
    if page and page > 1: params["page"] = page
    return urlencode(params)


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    db: Session = Depends(get_db),
):
    """Public marketing page only — never show app chrome / logout here."""
    region, region_source = detect_billing_region_detail(
        dict(request.headers), dict(request.cookies)
    )
    is_ng = region == "ng"
    return templates.TemplateResponse(
        request,
        "landing.html",
        template_ctx(
            request,
            None,
            db,
            region=region,
            region_source=region_source,
            free_clear=FREE_CLEAR_MATCHES,
            prices={
                "byok": "₦2,000" if is_ng else "$5",
                "platform": "₦5,000" if is_ng else "$10",
                "byok_ng": "₦2,000",
                "platform_ng": "₦5,000",
                "byok_intl": "$5",
                "platform_intl": "$10",
            },
        ),
    )


@router.get("/jobs", response_class=HTMLResponse)
def jobs_board(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
    status: str = Query(""),
    min_score: float | None = Query(None),
    q: str = Query(""),
    location: str = Query(""),
    remote_type: str = Query(""),
    employment_type: str = Query(""),
    experience_level: str = Query(""),
    page: int = Query(1, ge=1),
):
    cfg = load_user_settings(db, user)
    threshold = min_score if min_score is not None else float((cfg.get("search") or {}).get("min_match_score") or 65)
    threshold = max(0.0, min(100.0, threshold))
    status_norm = (status or "").strip().lower()
    if status_norm and status_norm not in {s.value for s in JobStatus}:
        status_norm = ""

    cards, total, cache_ready = search_job_cards(
        db,
        user,
        min_score=threshold,
        q=q,
        status=status_norm,
        location=location,
        remote_type=remote_type,
        employment_type=employment_type,
        experience_level=experience_level,
        page=page,
        page_size=JOBS_PAGE_SIZE,
    )
    if not cache_ready:
        active = get_active_profile(db, user)
        job = enqueue_job(
            db,
            kind="user_rescore",
            queue="matching",
            payload={"user_id": str(user.id)},
        )
        db.commit()

    total_pages = max(1, ceil(total / JOBS_PAGE_SIZE) if total else 1)
    page_num = min(page, total_pages)
    full_access = has_full_job_access(db, user)
    unlocked_ids = set() if full_access else free_unlocked_listing_ids(db, user)
    free_remaining = 0 if full_access else free_opens_remaining(db, user)
    free_used = 0 if full_access else len(unlocked_ids)

    job_rows = []
    for card in cards:
        lid = int(card.id)
        is_private_own = (
            card.visibility == ListingVisibility.PRIVATE.value
            and card.listing.owner_user_id == user.id
        )
        blurred = not (full_access or is_private_own or lid in unlocked_ids)
        openable = full_access or lid in unlocked_ids or is_private_own or (
            card.visibility == ListingVisibility.PUBLIC.value
            and len(unlocked_ids) < FREE_CLEAR_MATCHES
        )
        job_rows.append({"card": card, "blurred": blurred, "openable": openable, "unlocked": full_access or lid in unlocked_ids})

    status_counts = {s.value: 0 for s in JobStatus}
    if cards:
        for card in cards:
            status_counts[card.status] = status_counts.get(card.status, 0) + 1

    ai_ok, ai_reason = can_use_ai(db, user)
    suggest_min = max(0, int(threshold) - 10)
    def qs(page_value: int | None = None, *, status_value: str = status_norm, threshold_value: float = threshold) -> str:
        return _jobs_query_string(status=status_value, min_score=threshold_value, q=q, page=page_value, location=location, remote_type=remote_type, employment_type=employment_type, experience_level=experience_level)

    pager = {
        "page": page_num, "page_size": JOBS_PAGE_SIZE, "total": total, "total_pages": total_pages,
        "start": (page_num - 1) * JOBS_PAGE_SIZE + 1 if total else 0,
        "end": (page_num - 1) * JOBS_PAGE_SIZE + len(cards),
        "has_prev": page_num > 1, "has_next": page_num < total_pages,
        "prev_qs": qs(page_num - 1), "next_qs": qs(page_num + 1), "base_qs": qs(),
        "suggest_qs": qs(status_value="", threshold_value=suggest_min),
    }
    return templates.TemplateResponse(
        request,
        "jobs.html",
        template_ctx(
            request, user, db,
            jobs=job_rows, status_counts=status_counts, status=status_norm,
            min_score=threshold, q=q, pager=pager, ai_ok=ai_ok, ai_reason=ai_reason,
            suggest_min=suggest_min, full_access=full_access, free_clear=FREE_CLEAR_MATCHES,
            free_remaining=free_remaining, free_used=free_used,
            locked_count=0 if full_access else max(0, total - free_used),
            has_matches=bool(total), profile_ready=True, cache_ready=cache_ready,
            location=location, remote_type=remote_type,
            employment_type=employment_type, experience_level=experience_level,
        ),
    )


def _require_listing_access(db: Session, user: User, job_ref: str):
    card = get_user_job_card_by_public_id(db, user, job_ref)
    if not card:
        raise HTTPException(404, "Job not found")
    ok, reason = can_open_listing(db, user, card.id)
    if not ok:
        raise HTTPException(403, reason)
    return card


@router.get("/jobs/{job_ref}", response_class=HTMLResponse)
async def job_detail(
    job_ref: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
    from_qs: str = Query("", alias="from"),
):
    try:
        card = _require_listing_access(db, user, job_ref)
    except HTTPException as exc:
        if exc.status_code == 403:
            return flash_redirect("/billing", str(exc.detail))
        raise
    reasons = reasons_from_json(card.match_reasons)
    cfg = load_user_settings(db, user)
    profile = load_user_profile_dict(db, user)
    breakdown = score_job_detail(
        {"title": card.title, "description": card.description, "location": card.location},
        profile,
        cfg,
    )
    files = list_resume_files(card.output_dir)
    apply_profile = apply_profile_from_user(db, user)
    assist = apply_assist_payload(card, apply_profile)
    copy = await ensure_user_apply_copy(db, user, card)
    db.commit()
    return templates.TemplateResponse(
        request,
        "job_detail.html",
        template_ctx(
            request,
            user,
            db,
            job=card,
            reasons=reasons,
            breakdown=breakdown,
            files=files,
            assist=assist,
            cover_blurb=copy["cover_blurb"],
            app_answers=copy["answers"],
            apply_url=safe_http_url(card.url),
            jobs_from=from_qs,
            jobs_back=_jobs_list_path(from_qs),
        ),
    )


@router.post("/sync")
async def sync(
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    if not is_admin(user):
        return flash_redirect("/", "Only admins can sync the shared job catalogue.")
    result = await sync_public_jobs(db)
    flash = (
        f"Catalogue synced: fetched {result['fetched']}, "
        f"created {result['created']}, updated {result['updated']}, "
        f"reopened {result.get('reopened', 0)}, closed {result.get('closed', 0)}"
    )
    return flash_redirect("/admin", flash)


@router.post("/rescore")
def rescore(
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
):
    n = rescore_user_jobs(db, user)
    if n == 0:
        return flash_redirect(
            "/jobs",
            "Scores are computed live from the catalogue. Open a job to start tracking status.",
        )
    return flash_redirect("/jobs", f"Refreshed {n} saved ranking(s) for your profile")


@router.get("/paste", response_class=HTMLResponse)
def paste_form(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
):
    return templates.TemplateResponse(
        request,
        "paste.html",
        template_ctx(request, user, db, error=""),
    )


@router.post("/paste")
async def paste_submit(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
    title: str = Form(""),
    company: str = Form(""),
    location: str = Form(""),
    url: str = Form(""),
    description: str = Form(""),
):
    enforce("paste", user_id=user.id, redirect_path="/paste")
    if not url.strip() and not description.strip() and not title.strip():
        return templates.TemplateResponse(
            request,
            "paste.html",
            template_ctx(
                request,
                user,
                db,
                error="Provide a job URL, description, or title.",
            ),
            status_code=400,
        )
    # Strip non-http URLs to reduce SSRF risk on paste fetch path
    url_clean = safe_http_url(url) or ""
    if url.strip() and not url_clean:
        return templates.TemplateResponse(
            request,
            "paste.html",
            template_ctx(request, user, db, error="URL must start with http:// or https://"),
            status_code=400,
        )
    card = await add_pasted_job_for_user(
        db,
        user,
        title=title,
        company=company,
        location=location,
        url=url_clean,
        description=description,
    )
    return flash_redirect(job_path(card), "Private job added")


@router.post("/jobs/{job_ref}/generate")
async def generate_cv(
    job_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
):
    enforce("ai", user_id=user.id, redirect_path=f"/jobs/{job_ref}")
    try:
        card = _require_listing_access(db, user, job_ref)
    except HTTPException as exc:
        if exc.status_code == 403:
            return flash_redirect("/billing", str(exc.detail))
        raise
    ai_ok, reason = can_use_ai(db, user)
    if not ai_ok:
        return flash_redirect(f"/jobs/{job_ref}", reason)
    creds = llm_creds_for_user(db, user)
    profile = load_user_profile_dict(db, user)
    up = get_active_profile(db, user)
    display = (up.full_name if up else "") or user.full_name or profile.get("name") or "Candidate"
    if up:
        bits = [b for b in [up.email, up.phone, up.linkedin, up.github] if b]
        if bits:
            profile = {**profile, "name": display, "contact": " | ".join(bits)}
    out, used_fallback = await generate_resume_files(
        card,
        profile=profile,
        creds=creds,
        user_id=str(user.id),
        profile_id=up.id if up else None,
        display_name=display,
        db=db,
    )
    card.user_job.output_dir = str(out)
    card.user_job.status = JobStatus.CV_READY.value
    db.commit()
    msg = (
        "CV generated with rule-based fallback (AI rewrite unavailable)"
        if used_fallback
        else "CV generated"
    )
    return flash_redirect(f"/jobs/{job_ref}", msg)


@router.post("/jobs/{job_ref}/status")
def set_status(
    job_ref: str,
    status: str = Form(...),
    from_qs: str = Form("", alias="from"),
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
):
    try:
        card = _require_listing_access(db, user, job_ref)
    except HTTPException as exc:
        if exc.status_code == 403:
            return flash_redirect("/billing", str(exc.detail))
        raise
    allowed = {s.value for s in JobStatus}
    if status not in allowed:
        raise HTTPException(400, "Invalid status")
    card.user_job.status = status
    db.commit()
    if status in {JobStatus.APPLIED.value, JobStatus.SKIPPED.value}:
        return flash_redirect(_jobs_list_path(from_qs), "Status updated")
    return flash_redirect(f"/jobs/{job_ref}", "Status updated")


@router.post("/jobs/{job_ref}/rewrite/cover")
async def rewrite_cover(
    job_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
):
    enforce("ai", user_id=user.id, redirect_path=f"/jobs/{job_ref}")
    try:
        card = _require_listing_access(db, user, job_ref)
    except HTTPException as exc:
        if exc.status_code == 403:
            return flash_redirect("/billing", str(exc.detail))
        raise
    ai_ok, reason = can_use_ai(db, user)
    if not ai_ok:
        return flash_redirect(f"/jobs/{job_ref}", reason)
    await ensure_user_apply_copy(db, user, card, force_cover=True)
    db.commit()
    return flash_redirect(f"/jobs/{job_ref}", "Cover note rewritten")


@router.post("/jobs/{job_ref}/rewrite/answers")
async def rewrite_answers(
    job_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
):
    enforce("ai", user_id=user.id, redirect_path=f"/jobs/{job_ref}")
    try:
        card = _require_listing_access(db, user, job_ref)
    except HTTPException as exc:
        if exc.status_code == 403:
            return flash_redirect("/billing", str(exc.detail))
        raise
    ai_ok, reason = can_use_ai(db, user)
    if not ai_ok:
        return flash_redirect(f"/jobs/{job_ref}", reason)
    await ensure_user_apply_copy(db, user, card, force_answers=True)
    db.commit()
    return flash_redirect(f"/jobs/{job_ref}", "Application answers rewritten")


@router.post("/jobs/{job_ref}/rewrite/answer")
async def rewrite_one_answer(
    job_ref: str,
    question: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
):
    enforce("ai", user_id=user.id, redirect_path=f"/jobs/{job_ref}")
    try:
        card = _require_listing_access(db, user, job_ref)
    except HTTPException as exc:
        if exc.status_code == 403:
            return flash_redirect("/billing", str(exc.detail))
        raise
    ai_ok, reason = can_use_ai(db, user)
    if not ai_ok:
        return flash_redirect(f"/jobs/{job_ref}", reason)
    await ensure_user_apply_copy(db, user, card)
    existing = load_apply_draft_db(db, user, card.id)
    copy = await rewrite_answer_in_draft(
        card,
        question,
        apply_profile_from_user(db, user),
        creds=llm_creds_for_user(db, user),
        existing=existing,
    )
    save_apply_draft_db(db, user, card.id, copy)
    db.commit()
    return flash_redirect(f"/jobs/{job_ref}", f"Rewrote: {question}")


@router.post("/jobs/{job_ref}/save-copy")
async def save_apply_copy(
    job_ref: str,
    cover_blurb: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
):
    try:
        card = _require_listing_access(db, user, job_ref)
    except HTTPException as exc:
        if exc.status_code == 403:
            return flash_redirect("/billing", str(exc.detail))
        raise
    draft = await ensure_user_apply_copy(db, user, card, use_ai=False)
    payload = {"cover_blurb": cover_blurb, "answers": draft["answers"]}
    save_apply_draft_db(db, user, card.id, payload)
    db.commit()
    return flash_redirect(f"/jobs/{job_ref}", "Cover note saved")


@router.get("/jobs/{job_ref}/download/{filename}")
def download_file(
    job_ref: str,
    filename: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
):
    try:
        card = _require_listing_access(db, user, job_ref)
    except HTTPException as exc:
        if exc.status_code == 403:
            return flash_redirect("/billing", str(exc.detail))
        raise
    if not card.output_dir:
        raise HTTPException(404, "File not found")
    safe = Path(filename).name
    if Path(safe).suffix.lower() not in {".pdf", ".docx"}:
        raise HTTPException(404, "File not found")
    try:
        path = assert_download_under_user(user.id, card.output_dir, safe)
    except PermissionError:
        raise HTTPException(404, "File not found") from None
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "File not found")
    media = "application/octet-stream"
    if safe.endswith(".pdf"):
        media = "application/pdf"
    elif safe.endswith(".docx"):
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return FileResponse(path, media_type=media, filename=safe)
