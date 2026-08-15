"""Split styles.css into modular CSS files."""
import re
from pathlib import Path

CSS_DIR = Path(__file__).resolve().parents[1] / "app" / "static"
SRC = CSS_DIR / "styles.css"

lines = SRC.read_text(encoding="utf-8").splitlines(keepends=True)

# Find section boundaries by looking for /* ---------- ... ---------- */
sections = []
for i, line in enumerate(lines):
    m = re.match(r'/\*\s*-{5,}\s*(.+?)\s*-{5,}\s*\*/', line)
    if m:
        sections.append((i, m.group(1).strip()))

# Map sections to files
BASE_SECTIONS = {"root variables", "dark mode", "theme toggle", "form validation",
                 "skeleton loaders", "skip link", "shells", "sticky site header",
                 "buttons", "page head", "panels", "stats", "forms", "table",
                 "badges", "scores", "notices", "helpers"}
PAGES_SECTIONS = {"job detail", "auth", "onboarding", "billing", "tease",
                  "working overlay", "landing", "jobs list"}
RESPONSIVE_SECTIONS = {"mobile nav", "toast notifications", "score ring",
                       "scroll reveal", "flash polish", "reduced motion for new bits",
                       "large tablets / small laptops (≤1024px)", "tablets (≤768px)",
                       "small phones (≤480px)", "landing page button overrides",
                       "responsive — all screen sizes", "job detail page — improved layout"}

def find_section_range(name, sections, lines):
    """Find line range for a section."""
    for idx, (start, sec_name) in enumerate(sections):
        if sec_name.lower() == name.lower():
            end = sections[idx + 1][0] if idx + 1 < len(sections) else len(lines)
            return start, end
    return None, None

# Build output files
base_lines = []
components_lines = []
pages_lines = []
responsive_lines = []

# Header
HEADER = "/* ============================================================\n"

# First pass: determine which file each section goes to
section_map = {}
for i, (start, name) in enumerate(sections):
    end = sections[i + 1][0] if i + 1 < len(sections) else len(lines)
    lower = name.lower()
    if lower in BASE_SECTIONS or lower.startswith("job detail page"):
        section_map[i] = "components"
    elif lower in PAGES_SECTIONS:
        section_map[i] = "pages"
    elif lower in RESPONSIVE_SECTIONS:
        section_map[i] = "responsive"
    else:
        section_map[i] = "components"

# Collect pre-section content (root variables etc)
first_section_idx = sections[0][0] if sections else len(lines)
pre_section = lines[:first_section_idx]

# Now split by sections
for i, (start, name) in enumerate(sections):
    end = sections[i + 1][0] if i + 1 < len(sections) else len(lines)
    chunk = lines[start:end]
    target = section_map.get(i, "components")
    if target == "components":
        components_lines.extend(chunk)
    elif target == "pages":
        pages_lines.extend(chunk)
    elif target == "responsive":
        responsive_lines.extend(chunk)

# base.css = root vars + dark mode + reset
base_content = "".join(pre_section) + "\n"

# components.css = component sections
components_content = HEADER + "   Vitae — components\n   ============================================================ */\n\n" + "".join(components_lines)

# pages.css = page-specific sections
pages_content = HEADER + "   Vitae — page styles\n   ============================================================ */\n\n" + "".join(pages_lines)

# responsive.css = all media queries + responsive
responsive_content = HEADER + "   Vitae — responsive\n   ============================================================ */\n\n" + "".join(responsive_lines)

# Write files
(CSS_DIR / "base.css").write_text(base_content, encoding="utf-8")
(CSS_DIR / "components.css").write_text(components_content, encoding="utf-8")
(CSS_DIR / "pages.css").write_text(pages_content, encoding="utf-8")
(CSS_DIR / "responsive.css").write_text(responsive_content, encoding="utf-8")

# Update styles.css to be the import file
import_content = """/* ============================================================
   Vitae — styles entry point
   Imports modular CSS files in dependency order.
   ============================================================ */

@import url("base.css");
@import url("components.css");
@import url("pages.css");
@import url("responsive.css");
"""
(SRC).write_text(import_content, encoding="utf-8")

print("Split complete:")
for f in ["base.css", "components.css", "pages.css", "responsive.css", "styles.css"]:
    size = (CSS_DIR / f).stat().st_size
    print(f"  {f}: {size / 1024:.1f} KB")
