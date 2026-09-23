from __future__ import annotations

import uuid
from pathlib import Path

from app.config import project_path


def user_data_dir(user_id: uuid.UUID) -> Path:
    path = project_path("data", "users", str(user_id))
    path.mkdir(parents=True, exist_ok=True)
    (path / "outputs").mkdir(exist_ok=True)
    (path / "profiles").mkdir(exist_ok=True)
    return path


def profile_data_dir(user_id: uuid.UUID, profile_id: int) -> Path:
    path = user_data_dir(user_id) / "profiles" / str(profile_id)
    path.mkdir(parents=True, exist_ok=True)
    (path / "outputs").mkdir(exist_ok=True)
    (path / "resumes").mkdir(exist_ok=True)
    return path
