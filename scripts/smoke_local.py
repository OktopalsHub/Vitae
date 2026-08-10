"""Local smoke checks for the Vitae dashboard."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.main import app
from app.profile.loader import load_master_cv_path, load_or_build_profile


def _ok(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"[{status}] {name}{suffix}")
    if not cond:
        raise AssertionError(name)


def main() -> None:
    cv = load_master_cv_path()
    _ok("master CV exists", cv.exists(), str(cv))

    profile = load_or_build_profile(force=True)
    _ok("profile loads", bool(profile.get("name")), profile.get("name", ""))

    with TestClient(app) as client:
        r = client.get("/")
        _ok("GET /", r.status_code == 200, f"status={r.status_code}")
        _ok("GET / has jobs UI", "Match" in r.text or "jobs" in r.text.lower(), f"len={len(r.text)}")

        r = client.get("/paste")
        _ok("GET /paste", r.status_code == 200, f"status={r.status_code}")

        r = client.get("/settings")
        _ok("GET /settings", r.status_code == 200, f"status={r.status_code}")

        r = client.post(
            "/paste",
            data={
                "title": "Backend Engineer",
                "company": "SmokeTest Co",
                "location": "Remote",
                "url": "https://example.com/jobs/smoke-backend",
                "description": (
                    "We need a Node.js / TypeScript backend engineer with "
                    "PostgreSQL, Redis, REST APIs, and AWS experience."
                ),
            },
            follow_redirects=False,
        )
        _ok("POST /paste", r.status_code in (302, 303), f"status={r.status_code}")
        loc = r.headers.get("location", "")
        _ok("POST /paste redirects to job", loc.startswith("/jobs/"), loc)

        job_id = loc.rstrip("/").split("/")[-1].split("?")[0]
        r = client.get(f"/jobs/{job_id}")
        _ok("GET /jobs/{id}", r.status_code == 200, f"status={r.status_code}")
        _ok("job detail shows company", "SmokeTest" in r.text)

        r = client.post(f"/jobs/{job_id}/generate", follow_redirects=False)
        _ok("POST generate CV", r.status_code in (302, 303), f"status={r.status_code}")

        r = client.get(f"/jobs/{job_id}")
        _ok("job page after generate", r.status_code == 200, f"status={r.status_code}")
        has_download = ".pdf" in r.text or ".docx" in r.text or "download" in r.text.lower()
        _ok("generated files listed", has_download)

        r = client.post(f"/jobs/{job_id}/status", data={"status": "applied"}, follow_redirects=False)
        _ok("POST mark applied", r.status_code in (302, 303), f"status={r.status_code}")

        print("\nRunning job sync (may take 30–90s)...")
        r = client.post("/sync", follow_redirects=False, timeout=180.0)
        _ok("POST /sync", r.status_code in (302, 303), f"status={r.status_code} loc={r.headers.get('location','')}")

        r = client.get("/")
        _ok("GET / after sync", r.status_code == 200, f"status={r.status_code}")

    print("\nAll smoke checks passed.")


if __name__ == "__main__":
    main()
