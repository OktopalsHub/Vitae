from __future__ import annotations

from math import ceil
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.apply_assist import apply_assist_payload, rewrite_answer_in_draft, save_job_draft
from app.config import project_path
from app.db import get_db
from app.matching.scorer import reasons_from_json, score_job_detail
from app.models import JobStatus, User
from app.services import (
    add_pasted_job_for_user,
    get_user_job_card,
    list_job_cards,
    rescore_user_jobs,
    sync_public_jobs,
)
from app.tailor.generator import generate_resume_files, list_resume_files
from app.accounts import (
    apply_profile_from_user,
    can_open_listing,
    can_use_ai,
    detect_billing_region_detail,
    ensure_account,
    get_active_profile,
    has_full_job_access,
    is_profile_confirmed,
    llm_creds_for_user,
    load_user_profile_dict,
    load_user_settings,
    save_apply_draft_db,
    FREE_CLEAR_MATCHES,
)
from app.auth import optional_current_user
from app.web_helpers import (
    OnboardingRequired,
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


def _jobs_query_string(*, status: str, min_score: float, q: str, page: int | None = None) -> str:
    params: dict[str, str | float | int] = {"min_score": int(min_score) if float(min_score).is_integer() else min_score}
    if status:
        params["status"] = status
    if q:
        params["q"] = q
    if page and page > 1:
        params["page"] = page
    return urlencode(params)


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_current_user),
    status: str = Query(""),
    min_score: float | None = Query(None),
    q: str = Query(""),
    page: int = Query(1, ge=1),
):
    if user is None:
        region, region_source = detect_billing_region_detail(
            dict(request.headers), dict(request.cookies)
        )
        is_ng = region == "ng"
        return templates.TemplateResponse(
            "landing.html",
            template_ctx(
                request,
                None,
                db,
                region=region,
                region_source=region_source,
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
    ensure_account(db, user)
    if not is_profile_confirmed(db, user):
        raise OnboardingRequired()
    cfg = load_user_settings(db, user)
    threshold = (
        min_score
        if min_score is not None
        else float((cfg.get("search") or {}).get("min_match_score") or 65)
    )
    threshold = max(0.0, min(100.0, threshold))
    status_norm = (status or "").strip().lower()
    q_norm = (q or "").strip()
    allowed_statuses = {s.value for s in JobStatus}
    if status_norm and status_norm not in allowed_statuses:
        status_norm = ""

    # Open catalogue only (closed listings excluded in list_job_cards).
    baseline = list_job_cards(db, user, min_score=threshold)
    status_counts = {
        s.value: sum(1 for c in baseline if c.status == s.value) for s in JobStatus
    }
    filtered = baseline
    if q_norm:
        ql = q_norm.lower()
        filtered = [
            c
            for c in filtered
            if ql in f"{c.title} {c.company} {c.location}".lower()
        ]
    matched = (
        [c for c in filtered if c.status == status_norm]
        if status_norm
        else filtered
    )

    total = len(matched)
    total_pages = max(1, ceil(total / JOBS_PAGE_SIZE) if total else 1)
    page_num = min(page, total_pages)
    start = (page_num - 1) * JOBS_PAGE_SIZE
    page_cards = matched[start : start + JOBS_PAGE_SIZE]
    end = start + len(page_cards)

    full_access = has_full_job_access(db, user)
    clear_ids = (
        set()
        if full_access
        else {int(c.id) for c in baseline[:FREE_CLEAR_MATCHES]}
    )
    job_rows = [
        {
            "card": card,
            "blurred": not (full_access or card.id in clear_ids),
            "openable": full_access or card.id in clear_ids,
        }
        for card in page_cards
    ]

    ai_ok, _ = can_use_ai(db, user)
    suggest_min = max(0, int(threshold) - 10)
    pager = {
        "page": page_num,
        "page_size": JOBS_PAGE_SIZE,
        "total": total,
        "total_pages": total_pages,
        "start": start + 1 if total else 0,
        "end": end,
        "has_prev": page_num > 1,
        "has_next": page_num < total_pages,
        "prev_qs": _jobs_query_string(
            status=status_norm, min_score=threshold, q=q_norm, page=page_num - 1
        ),
        "next_qs": _jobs_query_string(
            status=status_norm, min_score=threshold, q=q_norm, page=page_num + 1
        ),
        "base_qs": _jobs_query_string(status=status_norm, min_score=threshold, q=q_norm),
        "suggest_qs": _jobs_query_string(status="", min_score=suggest_min, q=q_norm),
    }

    locked_count = (
        0 if full_access else sum(1 for c in matched if c.id not in clear_ids)
    )

    return templates.TemplateResponse(
        "jobs.html",
        template_ctx(
            request,
            user,
            db,
            jobs=job_rows,
            status_counts=status_counts,
            status=status_norm,
            min_score=threshold,
            q=q_norm,
            pager=pager,
            ai_ok=ai_ok,
            suggest_min=suggest_min,
            full_access=full_access,
            free_clear=FREE_CLEAR_MATCHES,
            locked_count=locked_count,
            has_matches=bool(baseline),
        ),
    )


def _require_listing_access(db: Session, user: User, listing_id: int):
    card = get_user_job_card(db, user, listing_id)
    if not card:
        raise HTTPException(404, "Job not found")
    ok, reason = can_open_listing(db, user, listing_id)
    if not ok:
        raise HTTPException(403, reason)
    return card


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
):
    try:
        card = _require_listing_access(db, user, job_id)
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
            "/",
            "Scores are computed live from the catalogue. Open a job to start tracking status.",
        )
    return flash_redirect("/", f"Refreshed {n} saved ranking(s) for your profile")


@router.get("/paste", response_class=HTMLResponse)
def paste_form(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
):
    return templates.TemplateResponse(
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
    if not url.strip() and not description.strip() and not title.strip():
        return templates.TemplateResponse(
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
    return flash_redirect(f"/jobs/{card.id}", "Private job added")


@router.post("/jobs/{job_id}/generate")
async def generate_cv(
    job_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
):
    try:
        card = _require_listing_access(db, user, job_id)
    except HTTPException as exc:
        if exc.status_code == 403:
            return flash_redirect("/billing", str(exc.detail))
        raise
    ai_ok, reason = can_use_ai(db, user)
    if not ai_ok:
        return flash_redirect(f"/jobs/{job_id}", reason)
    creds = llm_creds_for_user(db, user)
    profile = load_user_profile_dict(db, user)
    up = get_active_profile(db, user)
    display = (up.full_name if up else "") or user.full_name or profile.get("name") or "Candidate"
    if up:
        bits = [b for b in [up.email, up.phone, up.linkedin, up.github] if b]
        if bits:
            profile = {**profile, "name": display, "contact": " | ".join(bits)}
    out = await generate_resume_files(
        card,
        profile=profile,
        creds=creds,
        user_id=str(user.id),
        profile_id=up.id if up else None,
        display_name=display,
    )
    card.user_job.output_dir = str(out)
    card.user_job.status = JobStatus.CV_READY.value
    db.commit()
    return flash_redirect(f"/jobs/{job_id}", "CV generated")


@router.post("/jobs/{job_id}/status")
def set_status(
    job_id: int,
    status: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
):
    try:
        card = _require_listing_access(db, user, job_id)
    except HTTPException as exc:
        if exc.status_code == 403:
            return flash_redirect("/billing", str(exc.detail))
        raise
    allowed = {s.value for s in JobStatus}
    if status not in allowed:
        raise HTTPException(400, "Invalid status")
    card.user_job.status = status
    db.commit()
    return flash_redirect(f"/jobs/{job_id}", "Status updated")


@router.post("/jobs/{job_id}/rewrite/cover")
async def rewrite_cover(
    job_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
):
    try:
        card = _require_listing_access(db, user, job_id)
    except HTTPException as exc:
        if exc.status_code == 403:
            return flash_redirect("/billing", str(exc.detail))
        raise
    ai_ok, reason = can_use_ai(db, user)
    if not ai_ok:
        return flash_redirect(f"/jobs/{job_id}", reason)
    await ensure_user_apply_copy(db, user, card, force_cover=True)
    db.commit()
    return flash_redirect(f"/jobs/{job_id}", "Cover note rewritten")


@router.post("/jobs/{job_id}/rewrite/answers")
async def rewrite_answers(
    job_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
):
    try:
        card = _require_listing_access(db, user, job_id)
    except HTTPException as exc:
        if exc.status_code == 403:
            return flash_redirect("/billing", str(exc.detail))
        raise
    ai_ok, reason = can_use_ai(db, user)
    if not ai_ok:
        return flash_redirect(f"/jobs/{job_id}", reason)
    await ensure_user_apply_copy(db, user, card, force_answers=True)
    db.commit()
    return flash_redirect(f"/jobs/{job_id}", "Application answers rewritten")


@router.post("/jobs/{job_id}/rewrite/answer")
async def rewrite_one_answer(
    job_id: int,
    question: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
):
    try:
        card = _require_listing_access(db, user, job_id)
    except HTTPException as exc:
        if exc.status_code == 403:
            return flash_redirect("/billing", str(exc.detail))
        raise
    ai_ok, reason = can_use_ai(db, user)
    if not ai_ok:
        return flash_redirect(f"/jobs/{job_id}", reason)
    await ensure_user_apply_copy(db, user, card)
    copy = await rewrite_answer_in_draft(
        card,
        question,
        apply_profile_from_user(db, user),
        creds=llm_creds_for_user(db, user),
        user_id=str(user.id),
    )
    save_apply_draft_db(db, user, card.id, copy)
    db.commit()
    return flash_redirect(f"/jobs/{job_id}", f"Rewrote: {question}")


@router.post("/jobs/{job_id}/save-copy")
async def save_apply_copy(
    job_id: int,
    cover_blurb: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
):
    try:
        card = _require_listing_access(db, user, job_id)
    except HTTPException as exc:
        if exc.status_code == 403:
            return flash_redirect("/billing", str(exc.detail))
        raise
    draft = await ensure_user_apply_copy(db, user, card, use_ai=False)
    payload = {"cover_blurb": cover_blurb, "answers": draft["answers"]}
    save_job_draft(card.id, payload, user_id=str(user.id))
    save_apply_draft_db(db, user, card.id, payload)
    db.commit()
    return flash_redirect(f"/jobs/{job_id}", "Cover note saved")


@router.get("/jobs/{job_id}/download/{filename}")
def download_file(
    job_id: int,
    filename: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
):
    try:
        card = _require_listing_access(db, user, job_id)
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
