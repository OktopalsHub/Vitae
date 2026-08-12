from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from fastapi_users.db import (
    SQLAlchemyBaseOAuthAccountTableUUID,
    SQLAlchemyBaseUserTableUUID,
)
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid


class Base(DeclarativeBase):
    pass


class JobStatus(str, Enum):
    NEW = "new"
    CV_READY = "cv_ready"
    APPLIED = "applied"
    SKIPPED = "skipped"


class ListingVisibility(str, Enum):
    PUBLIC = "public"
    PRIVATE = "private"


class BillingPlan(str, Enum):
    NONE = "none"
    BYOK_MONTHLY = "byok_monthly"
    PLATFORM_MONTHLY = "platform_monthly"


class BillingRegion(str, Enum):
    NG = "ng"
    INTL = "intl"


class UserRole(str, Enum):
    BASIC = "basic"
    ADMIN = "admin"
    SUPER_ADMIN = "super_admin"


class User(SQLAlchemyBaseUserTableUUID, Base):
    # Plural — Postgres reserves USER; fastapi-users default is "user".
    __tablename__ = "users"

    full_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    role: Mapped[str] = mapped_column(String(32), default=UserRole.BASIC.value, index=True)
    # Points at profiles.id — each profile has its own subscription (ProfileBilling).
    active_profile_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    oauth_accounts: Mapped[list["OAuthAccount"]] = relationship(
        "OAuthAccount", lazy="joined", cascade="all, delete-orphan"
    )


class OAuthAccount(SQLAlchemyBaseOAuthAccountTableUUID, Base):
    # Parent mixin hardcodes ForeignKey("user.id"); keep in sync with User.__tablename__.
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="cascade"), nullable=False
    )


class JobListing(Base):
    """Shared platform catalogue (public) or private pasted listing."""

    __tablename__ = "job_listings"
    __table_args__ = (
        UniqueConstraint("source", "external_id", "scope_key", name="uq_listing_scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Unguessable URL token (not sequential). Used in /jobs/{public_id} routes.
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, default="")
    source: Mapped[str] = mapped_column(String(64), index=True)
    external_id: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(512))
    company: Mapped[str] = mapped_column(String(255), default="")
    location: Mapped[str] = mapped_column(String(255), default="")
    url: Mapped[str] = mapped_column(String(1024), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    salary: Mapped[str] = mapped_column(String(255), default="")
    visibility: Mapped[str] = mapped_column(
        String(16), default=ListingVisibility.PUBLIC.value, index=True
    )
    scope_key: Mapped[str] = mapped_column(String(64), default="public", index=True)
    owner_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="cascade"),
        nullable=True,
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class Profile(Base):
    """Career track for a user (e.g. Backend, Support, DevOps).

    Each profile has its own subscription + BYOK keys (ProfileBilling).
    """

    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="cascade"), index=True
    )
    label: Mapped[str] = mapped_column(String(128), default="Default")
    full_name: Mapped[str] = mapped_column(String(255), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    phone: Mapped[str] = mapped_column(String(64), default="")
    linkedin: Mapped[str] = mapped_column(String(512), default="")
    github: Mapped[str] = mapped_column(String(512), default="")
    website: Mapped[str] = mapped_column(String(512), default="")
    location_preference: Mapped[str] = mapped_column(String(255), default="")
    years_experience: Mapped[str] = mapped_column(String(64), default="4+")
    work_authorization: Mapped[str] = mapped_column(String(512), default="")
    salary_expectation: Mapped[str] = mapped_column(String(255), default="")
    earliest_start: Mapped[str] = mapped_column(String(128), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    master_cv_path: Mapped[str] = mapped_column(String(1024), default="")
    profile_json: Mapped[str] = mapped_column(Text, default="{}")
    career_facts_json: Mapped[str] = mapped_column(Text, default="[]")
    # One-time free listing opens for this profile (JSON list of listing ids). Not monthly.
    free_unlocked_json: Mapped[str] = mapped_column(Text, default="[]")
    profile_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


# Back-compat alias while call sites migrate to Profile / get_active_profile.
UserProfile = Profile


class UserJob(Base):
    """Thin ranking/status overlay scoped to a profile (and owning user)."""

    __tablename__ = "user_jobs"
    __table_args__ = (UniqueConstraint("profile_id", "listing_id", name="uq_profile_listing"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="cascade"), index=True
    )
    profile_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("profiles.id", ondelete="cascade"), nullable=True, index=True
    )
    listing_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("job_listings.id", ondelete="cascade"), index=True
    )
    match_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    match_reasons: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default=JobStatus.NEW.value, index=True)
    output_dir: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    scored_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    listing: Mapped[JobListing] = relationship("JobListing", lazy="joined")


class UserSettings(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="cascade"), primary_key=True
    )
    settings_json: Mapped[str] = mapped_column(Text, default="{}")


class ApplyDraft(Base):
    __tablename__ = "apply_drafts"
    __table_args__ = (UniqueConstraint("profile_id", "listing_id", name="uq_profile_draft_listing"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="cascade"), index=True
    )
    profile_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("profiles.id", ondelete="cascade"), nullable=True, index=True
    )
    listing_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("job_listings.id", ondelete="cascade"), index=True
    )
    cover_blurb: Mapped[str] = mapped_column(Text, default="")
    answers_json: Mapped[str] = mapped_column(Text, default="[]")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class ListingMatchScore(Base):
    """Persisted browse-rank score for a profile×listing (not a UserJob overlay)."""

    __tablename__ = "listing_match_scores"
    __table_args__ = (
        UniqueConstraint("profile_id", "listing_id", name="uq_profile_listing_score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("profiles.id", ondelete="cascade"), index=True
    )
    listing_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("job_listings.id", ondelete="cascade"), index=True
    )
    match_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    match_reasons: Mapped[str] = mapped_column(Text, default="")
    fingerprint: Mapped[str] = mapped_column(String(64), default="", index=True)
    scored_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ProfileBilling(Base):
    """One subscription (+ BYOK keys) per career profile."""

    __tablename__ = "profile_billing"

    profile_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("profiles.id", ondelete="cascade"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="cascade"), index=True
    )
    plan: Mapped[str] = mapped_column(String(32), default=BillingPlan.NONE.value, index=True)
    billing_region: Mapped[str] = mapped_column(String(16), default=BillingRegion.NG.value)
    llm_provider: Mapped[str] = mapped_column(String(32), default="openai")
    llm_key_encrypted: Mapped[str] = mapped_column(Text, default="")
    bachs_customer_id: Mapped[str] = mapped_column(String(128), default="")
    bachs_subscription_id: Mapped[str] = mapped_column(String(128), default="")
    subscription_status: Mapped[str] = mapped_column(String(64), default="")
    last_checkout_id: Mapped[str] = mapped_column(String(128), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


# Legacy name — prefer ProfileBilling. Kept for import shims during migration.
UserBilling = ProfileBilling


@dataclass
class JobCard:
    """Read model: catalogue listing + personal ranking/status (overlay optional)."""

    listing: JobListing
    match_score: float = 0.0
    match_reasons: str = ""
    status: str = JobStatus.NEW.value
    output_dir: str | None = None
    user_job: UserJob | None = None

    @property
    def id(self) -> int:
        return self.listing.id

    @property
    def public_id(self) -> str:
        return self.listing.public_id or ""

    @property
    def source(self) -> str:
        return self.listing.source

    @property
    def title(self) -> str:
        return self.listing.title

    @property
    def company(self) -> str:
        """Hide board/platform placeholders — never show ingest source names in UI."""
        return public_company_name(self.listing.company, self.listing.source)

    @property
    def location(self) -> str:
        return self.listing.location

    @property
    def url(self) -> str:
        return self.listing.url

    @property
    def description(self) -> str:
        return self.listing.description

    @property
    def salary(self) -> str:
        return self.listing.salary

    @property
    def visibility(self) -> str:
        return self.listing.visibility


_PLATFORM_COMPANY_PLACEHOLDERS = frozenset(
    {
        "djinni listing",
        "wellfound listing",
        "wellfound startup",
        "ziprecruiter",
        "remoteok",
        "remote ok",
        "remotive",
        "adzuna",
        "arbeitnow",
        "jobicy",
        "jooble",
        "greenhouse",
        "lever",
        "djinni",
        "wellfound",
        "weworkremotely",
        "we work remotely",
        "yc",
        "ycombinator",
        "y combinator",
        "paste",
        "unknown",
        "unknown company",
        "n/a",
        "na",
        "tba",
        "company tba",
    }
)

_PLATFORM_SUFFIXES = (" listing", " startup", " jobs", " careers", " board")


def public_company_name(company: str | None, source: str | None = None) -> str:
    """Strip board/platform labels so listings never expose where we ingested from."""
    raw = (company or "").strip()
    if not raw:
        return ""
    key = " ".join(raw.lower().split())
    if key in _PLATFORM_COMPANY_PLACEHOLDERS:
        return ""
    for suffix in _PLATFORM_SUFFIXES:
        if key.endswith(suffix):
            stem = key[: -len(suffix)].strip()
            if stem in _PLATFORM_COMPANY_PLACEHOLDERS:
                return ""
    src = (source or "").strip().lower()
    src_base = src.split(":", 1)[0] if src else ""
    if src and key == src:
        return ""
    if src_base and key == src_base:
        return ""
    if src_base and src_base in _PLATFORM_COMPANY_PLACEHOLDERS and key.replace("-", "") == src_base.replace(
        "-", ""
    ):
        return ""
    return raw
