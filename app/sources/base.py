from __future__ import annotations

from dataclasses import asdict, dataclass, field
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
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
