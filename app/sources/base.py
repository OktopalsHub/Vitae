from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class RawJob:
    source: str
    external_id: str
    title: str
    company: str = ""
    location: str = ""
    url: str = ""
    description: str = ""
    salary: str = ""
    normalized_location: str = ""
    employment_type: str = ""
    remote_type: str = ""
    experience_level: str = ""
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str = ""
    posted_at: datetime | None = None
    expires_at: datetime | None = None
    source_updated_at: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
