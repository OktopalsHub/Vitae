from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.applications import (
    answers_for_application,
    application_events,
    get_application,
    get_or_create_application,
    list_applications,
    refresh_application_snapshot,
    transition_application,
)
from app.config import project_path
from app.db import get_db
from app.models import ApplicationStatus, JobListing, User
from app.web_helpers import (
    flash_redirect,
    require_profile_ready,
    safe_http_url,
    template_ctx,
)

router = APIRouter(prefix="/applications", tags=["applications"])
templates = Jinja2Templates(directory=str(project_path("app", "templates")))
templates.env.filters["safe_http_url"] = safe_http_url

_STATUSES = {item.value for item in ApplicationStatus}


@router.get("", response_class=HTMLResponse)
def applications_board(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
    status: str = "",
):
    status = (status or "").strip().lower()
    if status and status not in _STATUSES:
        raise HTTPException(400, "Invalid application status")

    rows = list_applications(db, user, status=status)
    listing_ids = [row.listing_id for row in rows if row.listing_id is not None]
    listings = (
        db.query(JobListing)
        .filter(JobListing.id.in_(listing_ids))
        .all()
        if listing_ids
        else []
    )
    listing_map = {item.id: item for item in listings}
    items = [
        {"application": row, "listing": listing_map.get(row.listing_id)}
        for row in rows
    ]
    return templates.TemplateResponse(
        request,
        "applications.html",
        template_ctx(
            request,
            user,
            db,
            applications=items,
            status=status,
            statuses=sorted(_STATUSES),
        ),
    )


@router.post("/from-job/{job_ref}")
def create_from_job(
    job_ref: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
):
    listing = (
        db.query(JobListing)
        .filter(JobListing.public_id == (job_ref or "").strip())
        .one_or_none()
    )
    if listing is None:
        raise HTTPException(404, "Job not found")
    try:
        row = get_or_create_application(db, user, listing.id)
        db.commit()
    except PermissionError:
        db.rollback()
        raise HTTPException(404, "Job not found") from None
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    return flash_redirect(f"/applications/{row.id}", "Application saved")


@router.get("/{application_id}", response_class=HTMLResponse)
def application_detail(
    application_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
):
    try:
        row = get_application(db, user, application_id)
        listing = db.get(JobListing, row.listing_id) if row.listing_id else None
        events = application_events(db, user, row.id)
    except PermissionError:
        raise HTTPException(404, "Application not found") from None
    return templates.TemplateResponse(
        request,
        "application_detail.html",
        template_ctx(
            request,
            user,
            db,
            application=row,
            listing=listing,
            events=events,
            answers=answers_for_application(row),
            statuses=sorted(_STATUSES),
        ),
    )


@router.post("/{application_id}/refresh")
def refresh(
    application_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
):
    try:
        refresh_application_snapshot(db, user, application_id)
        db.commit()
    except PermissionError:
        db.rollback()
        raise HTTPException(404, "Application not found") from None
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    return flash_redirect(f"/applications/{application_id}", "Application snapshot refreshed")


@router.post("/{application_id}/status")
def change_status(
    application_id: int,
    status: str = Form(...),
    note: str = Form(""),
    external_application_id: str = Form(""),
    external_url: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
):
    try:
        row = transition_application(
            db,
            user,
            application_id,
            status,
            note=note,
            external_application_id=external_application_id,
            external_url=safe_http_url(external_url) if external_url else "",
        )
        db.commit()
    except PermissionError:
        db.rollback()
        raise HTTPException(404, "Application not found") from None
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    return flash_redirect(f"/applications/{row.id}", f"Application moved to {row.status.replace('_', ' ')}")
