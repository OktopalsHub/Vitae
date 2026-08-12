"""Compile-check every Jinja template to catch syntax errors from UI updates."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.main import templates

failures = []
count = 0
for f in sorted((ROOT / "app" / "templates").rglob("*.html")):
    rel = f.relative_to(ROOT / "app" / "templates").as_posix()
    count += 1
    try:
        templates.env.get_template(rel)
    except Exception as exc:
        failures.append((rel, str(exc)))
        print(f"[FAIL] {rel}: {exc}")

print(f"\nChecked {count} templates, {len(failures)} failures.")
if failures:
    sys.exit(1)
print("All templates compile OK.")