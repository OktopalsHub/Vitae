"""Seed an account (role optional). No legacy job-table migration.

Usage:
  python -m app.seed --email you@example.com --password 'secret123' --name 'Your Name'
  python -m app.seed --email admin@example.com --password 'secret123' --role super_admin
"""

from __future__ import annotations

import argparse
import asyncio
import uuid

from fastapi_users.exceptions import UserNotExists

from app.auth import UserCreate, UserManager, get_async_session, get_user_db
from app.db import SessionLocal, init_db
from app.models import User, UserRole
from app.accounts import ensure_account
from app.roles import apply_role, user_role


async def _ensure_user(email: str, password: str, full_name: str) -> uuid.UUID:
    async for session in get_async_session():
        async for user_db in get_user_db(session):
            manager = UserManager(user_db)
            try:
                existing = await manager.get_by_email(email)
                return existing.id
            except UserNotExists:
                pass
            user = await manager.create(
                UserCreate(
                    email=email,
                    password=password,
                    full_name=full_name,
                ),
                safe=True,
            )
            return user.id
    raise RuntimeError("Could not create user")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Vitae user account")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--name", default="")
    parser.add_argument(
        "--role",
        choices=[UserRole.BASIC.value, UserRole.ADMIN.value, UserRole.SUPER_ADMIN.value],
        default=UserRole.BASIC.value,
        help="Account role (default: basic)",
    )
    args = parser.parse_args()

    init_db()
    user_id = asyncio.run(_ensure_user(args.email, args.password, args.name))
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        assert user
        apply_role(user, args.role)
        ensure_account(db, user)
        db.add(user)
        db.commit()
        print(f"User ready: {args.email} ({user_id}) role={user_role(user)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
