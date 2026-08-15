"""Company-specific fetchers: YC, Wellfound, ZipRecruiter, BruntWork.

Import from the individual modules directly in new code:
  - app.sources.yc
  - app.sources.wellfound
  - app.sources.ziprecruiter
  - app.sources.bruntwork

This shim exists so existing call-sites continue to work without changes.
"""

from __future__ import annotations

from app.sources.yc import fetch_yc
from app.sources.wellfound import fetch_wellfound
from app.sources.ziprecruiter import fetch_ziprecruiter
from app.sources.bruntwork import (
    _bruntwork_job_links,
    _dedupe_text_lines,
    _parse_bruntwork_detail,
    fetch_bruntwork,
)

__all__ = [
    "fetch_yc",
    "fetch_wellfound",
    "fetch_ziprecruiter",
    "_bruntwork_job_links",
    "_dedupe_text_lines",
    "_parse_bruntwork_detail",
    "fetch_bruntwork",
]
