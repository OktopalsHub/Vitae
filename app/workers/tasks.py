from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from app.db import SessionLocal
from app.models import AutoApplyItem, AutoApplyItemStatus, AutoApplyRun, AutoApplyRunStatus, JobListing, ResumeArtifact, ResumeGeneration, User
from app.services import rescore_user_jobs, sync_public_jobs
from app.generator import generate_resume_files
from app.accounts import get_active_profile, load_user_profile_dict, llm_creds_for_user
from app.auto_apply import mark_item_review

log = logging.getLogger(__name__)


async def execute_job(kind: str, payload: dict[str, Any]) -> None:
    if kind == "catalogue_sync":
        with SessionLocal() as db:
            await sync_public_jobs(db)
        return
    if kind == "user_rescore":
        with SessionLocal() as db:
            user = db.get(User, uuid.UUID(str(payload["user_id"])))
            if user:
                rescore_user_jobs(db, user)
        return
    if kind == "auto_apply_prepare":
        await _prepare_auto_apply_item(int(payload["item_id"]))
        return
    raise ValueError(f"Unknown worker job kind: {kind}")


async def _prepare_auto_apply_item(item_id: int) -> None:
    # Preparation is intentionally idempotent. Existing READY/REVIEW items are not regenerated.
    with SessionLocal() as db:
        item = db.get(AutoApplyItem, item_id)
        if not item:
            return
        if item.status in {
            AutoApplyItemStatus.READY.value,
            AutoApplyItemStatus.NEEDS_REVIEW.value,
            AutoApplyItemStatus.SUBMITTED.value,
            AutoApplyItemStatus.SKIPPED.value,
        }:
            return
        run = db.get(AutoApplyRun, item.run_id)
        if not run or run.status in {AutoApplyRunStatus.CANCELLED.value, AutoApplyRunStatus.PAUSED.value}:
            return
        user = db.get(User, run.user_id)
        profile = get_active_profile(db, user) if user else None
        listing = item.listing_id
        if not user or not profile:
            item.status = AutoApplyItemStatus.FAILED.value
            item.last_error = "Profile is not available"
            db.commit()
            return
        item.status = AutoApplyItemStatus.PREPARING.value
        db.commit()

    # Keep the first implementation conservative: prepare the existing application
    # data and CV generation outside the request process. External submission remains manual.
    try:
        with SessionLocal() as db:
            item = db.get(AutoApplyItem, item_id)
            if not item:
                return
            run = db.get(AutoApplyRun, item.run_id)
            user = db.get(User, run.user_id) if run else None
            profile = get_active_profile(db, user) if user else None
            job = db.get(JobListing, item.listing_id)
            if not item or not run or not user or not profile or not job:
                raise ValueError("Auto-apply preparation context is missing")
            profile_dict = load_user_profile_dict(db, user)
            creds = llm_creds_for_user(db, user)
            display = profile.full_name or user.full_name or profile_dict.get("name") or "Candidate"
            out, fallback = await generate_resume_files(
                job,
                profile=profile_dict,
                creds=creds,
                user_id=str(user.id),
                profile_id=profile.id,
                display_name=display,
                db=db,
            )
            from app.applications import get_or_create_application
            application = get_or_create_application(db, user, job.id, channel="auto_apply")
            generation = db.query(ResumeGeneration).filter(
                ResumeGeneration.profile_id == profile.id,
                ResumeGeneration.listing_id == job.id,
            ).order_by(ResumeGeneration.created_at.desc()).first()
            artifact = None
            if generation:
                artifact = db.query(ResumeArtifact).filter(
                    ResumeArtifact.generation_id == generation.id,
                    ResumeArtifact.format == "pdf",
                ).one_or_none()
            application.resume_artifact_id = artifact.id if artifact else None
            item.application_id = application.id
            item.status = (
                AutoApplyItemStatus.NEEDS_REVIEW.value
                if run.requires_review
                else AutoApplyItemStatus.READY.value
            )
            item.requires_review = bool(run.requires_review)
            item.review_reason = "Review generated CV and application answers before submission." if run.requires_review else ""
            run.prepared_count = (
                db.query(AutoApplyItem)
                .filter(AutoApplyItem.run_id == run.id, AutoApplyItem.status.in_([
                    AutoApplyItemStatus.READY.value,
                    AutoApplyItemStatus.NEEDS_REVIEW.value,
                    AutoApplyItemStatus.SUBMITTED.value,
                ]))
                .count()
            )
            db.commit()
    except Exception:
        log.exception("Auto-apply preparation failed for item %s", item_id)
        with SessionLocal() as db:
            item = db.get(AutoApplyItem, item_id)
            if item:
                item.status = AutoApplyItemStatus.FAILED.value
                item.last_error = "Preparation failed. Retry the item."
                run = db.get(AutoApplyRun, item.run_id)
                if run:
                    run.failed_count = db.query(AutoApplyItem).filter(
                        AutoApplyItem.run_id == run.id,
                        AutoApplyItem.status == AutoApplyItemStatus.FAILED.value,
                    ).count()
                db.commit()
        raise
