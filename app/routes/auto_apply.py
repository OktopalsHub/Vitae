from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auto_apply import (
    cancel_run,
    create_run,
    get_run,
    list_items,
    list_runs,
    mark_item_review,
    mark_item_submitted,
    pause_run,
    start_run,
)
from app.config import project_path
from app.db import get_db
from app.models import JobListing, User
from app.web_helpers import flash_redirect, require_profile_ready, safe_http_url, template_ctx

router = APIRouter(prefix="/auto-apply", tags=["auto-apply"])
templates = Jinja2Templates(directory=str(project_path("app", "templates")))


@router.get("", response_class=HTMLResponse)
def board(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
):
    runs = list_runs(db, user)
    return templates.TemplateResponse(
        request,
        "auto_apply.html",
        template_ctx(request, user, db, runs=runs),
    )


@router.post("/runs")
def create(
    listing_ids: str = Form(...),
    max_applications: int = Form(10),
    requires_review: bool = Form(True),
    idempotency_key: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
):
    try:
        ids = [int(value.strip()) for value in listing_ids.split(",") if value.strip()]
        run = create_run(
            db,
            user,
            ids,
            max_applications=max_applications,
            requires_review=requires_review,
            idempotency_key=idempotency_key,
        )
        db.commit()
    except PermissionError as exc:
        db.rollback()
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    return flash_redirect(f"/auto-apply/runs/{run.id}", "Auto-apply batch created")


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def detail(
    run_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
):
    try:
        run = get_run(db, user, run_id)
        items = list_items(db, user, run_id)
    except PermissionError:
        raise HTTPException(404, "Auto-apply run not found") from None
    listing_ids = [item.listing_id for item in items]
    listings = db.query(JobListing).filter(JobListing.id.in_(listing_ids)).all() if listing_ids else []
    listing_map = {item.id: item for item in listings}
    return templates.TemplateResponse(
        request,
        "auto_apply_detail.html",
        template_ctx(
            request, user, db, run=run, items=items, listing_map=listing_map
        ),
    )


@router.post("/runs/{run_id}/start")
def start(run_id: int, db: Session = Depends(get_db), user: User = Depends(require_profile_ready)):
    try:
        start_run(db, user, run_id)
        db.commit()
    except (ValueError, PermissionError) as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    return flash_redirect(f"/auto-apply/runs/{run_id}", "Auto-apply run started")


@router.post("/runs/{run_id}/pause")
def pause(run_id: int, db: Session = Depends(get_db), user: User = Depends(require_profile_ready)):
    try:
        pause_run(db, user, run_id)
        db.commit()
    except (ValueError, PermissionError) as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    return flash_redirect(f"/auto-apply/runs/{run_id}", "Auto-apply run paused")


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: int, db: Session = Depends(get_db), user: User = Depends(require_profile_ready)):
    try:
        cancel_run(db, user, run_id)
        db.commit()
    except PermissionError:
        db.rollback()
        raise HTTPException(404, "Auto-apply run not found") from None
    return flash_redirect(f"/auto-apply/runs/{run_id}", "Auto-apply run cancelled")


@router.post("/items/{item_id}/review")
def review(
    item_id: int,
    reason: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
):
    try:
        item = mark_item_review(db, user, item_id, reason=reason)
        db.commit()
    except PermissionError:
        db.rollback()
        raise HTTPException(404, "Auto-apply item not found") from None
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    return flash_redirect(f"/auto-apply/runs/{item.run_id}", "Item marked for review")


@router.post("/items/{item_id}/submit")
def submit(
    item_id: int,
    external_application_id: str = Form(""),
    external_url: str = Form(""),
    note: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_profile_ready),
):
    try:
        item = mark_item_submitted(
            db,
            user,
            item_id,
            external_application_id=external_application_id,
            external_url=safe_http_url(external_url) if external_url else "",
            note=note,
        )
        db.commit()
    except PermissionError:
        db.rollback()
        raise HTTPException(404, "Auto-apply item not found") from None
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    return flash_redirect(f"/auto-apply/runs/{item.run_id}", "Application marked submitted")
