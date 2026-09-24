"""Admin + super-admin HTML surfaces."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import project_path
from app.db import get_db
from app.models import JobListing, ListingVisibility, ProfileBilling, User, UserJob, UserRole, WorkerJob, WorkerJobStatus
from app.roles import ALLOWED_ROLES, apply_role, can_assign_role, user_role
from app.web_helpers import flash_redirect, require_admin_user, require_super_admin_user, template_ctx
from datetime import datetime, timedelta

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory=str(project_path("app", "templates")))


@router.get("", response_class=HTMLResponse)
def admin_home(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin_user),
):
    public_listings = (
        db.query(func.count(JobListing.id))
        .filter(
            JobListing.visibility == ListingVisibility.PUBLIC.value,
            JobListing.is_active.is_(True),
        )
        .scalar()
        or 0
    )
    closed_listings = (
        db.query(func.count(JobListing.id))
        .filter(
            JobListing.visibility == ListingVisibility.PUBLIC.value,
            JobListing.is_active.is_(False),
        )
        .scalar()
        or 0
    )
    user_jobs = db.query(func.count(UserJob.id)).scalar() or 0
    users_count = db.query(func.count(User.id)).scalar() or 0
    paid_count = (
        db.query(func.count(ProfileBilling.profile_id))
        .filter(ProfileBilling.plan != "none")
        .scalar()
        or 0
    )
    role_counts = {
        UserRole.BASIC.value: 0,
        UserRole.ADMIN.value: 0,
        UserRole.SUPER_ADMIN.value: 0,
    }
    for row in db.query(User.role, func.count(User.id)).group_by(User.role).all():
        key = (row[0] or UserRole.BASIC.value).lower()
        if key == "user":
            key = UserRole.BASIC.value
        if key in role_counts:
            role_counts[key] = int(row[1])

    return templates.TemplateResponse(
        request,
        "admin/home.html",
        template_ctx(
            request,
            user,
            db,
            metrics={
                "public_listings": public_listings,
                "closed_listings": closed_listings,
                "user_jobs": user_jobs,
                "users": users_count,
                "paid": paid_count,
                "roles": role_counts,
            },
        ),
    )


@router.get("/users", response_class=HTMLResponse)
def admin_users(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_super_admin_user),
):
    rows = db.query(User).order_by(User.email.asc()).limit(500).all()
    people = [
        {
            "id": str(u.id),
            "email": u.email,
            "full_name": u.full_name or "",
            "role": user_role(u),
            "active": bool(u.is_active),
            "is_self": u.id == user.id,
        }
        for u in rows
    ]
    return templates.TemplateResponse(
        request,
        "admin/users.html",
        template_ctx(
            request,
            user,
            db,
            people=people,
            roles=sorted(ALLOWED_ROLES),
        ),
    )


@router.post("/users/{user_id}/role")
def set_user_role(
    user_id: uuid.UUID,
    role: str = Form(...),
    db: Session = Depends(get_db),
    actor: User = Depends(require_super_admin_user),
):
    target = db.get(User, user_id)
    if target is None:
        return flash_redirect("/admin/users", "User not found.")
    ok, reason = can_assign_role(actor, target, role)
    if not ok:
        return flash_redirect("/admin/users", reason)
    apply_role(target, role)
    db.add(target)
    db.commit()
    return flash_redirect("/admin/users", f"Updated {target.email} → {user_role(target)}")


@router.get("/operations/workers")
def worker_operations(
    db: Session = Depends(get_db),
    user: User = Depends(require_admin_user),
):
    """Return queue health counters for operators."""
    counts = {
        status: int(
            db.query(func.count(WorkerJob.id))
            .filter(WorkerJob.status == status)
            .scalar()
            or 0
        )
        for status in (
            WorkerJobStatus.QUEUED.value,
            WorkerJobStatus.RUNNING.value,
            WorkerJobStatus.COMPLETED.value,
            WorkerJobStatus.DEAD.value,
        )
    }
    stale_before = datetime.utcnow() - timedelta(minutes=10)
    stale_running = (
        db.query(func.count(WorkerJob.id))
        .filter(
            WorkerJob.status == WorkerJobStatus.RUNNING.value,
            func.coalesce(WorkerJob.last_heartbeat_at, WorkerJob.locked_at) < stale_before,
        )
        .scalar()
        or 0
    )
    return {
        "counts": counts,
        "stale_running": int(stale_running),
        "checked_at": datetime.utcnow().isoformat() + "Z",
    }


@router.post("/operations/workers/{job_id}/retry")
def retry_dead_worker_job(
    job_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin_user),
):
    """Requeue a dead job without changing its original payload."""
    job = db.get(WorkerJob, job_id)
    if job is None:
        return flash_redirect("/admin", "Worker job not found.")
    if job.status != WorkerJobStatus.DEAD.value:
        return flash_redirect("/admin", "Only dead worker jobs can be retried.")
    if job.attempts >= job.max_attempts:
        job.attempts = 0
    job.status = WorkerJobStatus.QUEUED.value
    job.available_at = datetime.utcnow()
    job.locked_at = None
    job.last_heartbeat_at = None
    job.lock_owner = ""
    job.failed_at = None
    db.commit()
    return flash_redirect("/admin", f"Worker job {job.id} requeued.")
