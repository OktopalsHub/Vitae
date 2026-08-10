from __future__ import annotations

import re

TITLE_INCLUDE = (
    r"\bbackend\b",
    r"\bback[\s-]?end\b",
    r"\bnode\.?js\b",
    r"\bnodejs\b",
    r"\btypescript\b",
    r"\bnestjs\b",
    r"\bsoftware engineer\b",
    r"\bsoftware developer\b",
    r"\bfull[\s-]?stack\b",
    r"\bapi engineer\b",
    r"\bplatform engineer\b",
    r"\bstaff engineer\b",
    r"\bprincipal engineer\b",
    r"\bdevops engineer\b",  # kept optional; scorer may downrank later
)

# Strong backend signals preferred in scorer title gate
STRONG_TITLE = (
    r"\bbackend\b",
    r"\bback[\s-]?end\b",
    r"\bnode\.?js\b",
    r"\bnodejs\b",
    r"\btypescript\b",
    r"\bnestjs\b",
    r"\bapi engineer\b",
    r"\bsoftware engineer\b",
    r"\bsoftware developer\b",
    r"\bfull[\s-]?stack\b",
    r"\bstaff engineer\b",
    r"\bprincipal engineer\b",
)

TITLE_EXCLUDE = (
    r"\bcustomer success\b",
    r"\bcustomer engineer\b",
    r"\bcustomer experience\b",
    r"\bsolutions engineer\b",
    r"\bsales\b",
    r"\bpre[\s-]?sales\b",
    r"\baccount executive\b",
    r"\bproduct owner\b",
    r"\bproduct manager\b",
    r"\bproject manager\b",
    r"\btechnical writer\b",
    r"\bdocumentation\b",
    r"\brecruiter\b",
    r"\btalent\b",
    r"\bmarketing\b",
    r"\bintern\b",
    r"\binternship\b",
    r"\bsupport engineer\b",
    r"\bservice sales\b",
    r"\bdesigner\b",
    r"\bux\b",
    r"\bui designer\b",
)

# Titles clearly owned by another language stack (skip ingest unless Node/TS also in title).
FOREIGN_STACK_TITLE = (
    r"\bgolang\b",
    r"\bgo(?:lang)?\s+(?:backend|software|platform|api|staff|principal|senior|junior)?\s*(?:engineer|developer)\b",
    r"\b(?:backend|software|platform|api)\s+(?:engineer|developer)\s*[-–—(].*\bgo(?:lang)?\b",
    r"\bjava\s+(?:backend|software|platform|api|staff|principal)?\s*(?:engineer|developer)\b",
    r"\b(?:backend|software)\s+(?:engineer|developer)\s*[-–—(].*\bjava\b",
    r"\bruby\s+on\s+rails\b",
    r"\brails\s+(?:engineer|developer)\b",
    r"\b\.net\s+(?:engineer|developer)\b",
    r"\bc#\s+(?:engineer|developer)\b",
    r"\bkotlin\s+(?:engineer|developer)\b",
    r"\bscala\s+(?:engineer|developer)\b",
    r"\bphp\s+(?:engineer|developer)\b",
    r"\brust\s+(?:engineer|developer)\b",
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def is_excluded_title(title: str, extra_patterns: list[str] | None = None) -> bool:
    t = _norm(title)
    patterns = list(TITLE_EXCLUDE)
    for p in extra_patterns or []:
        if p:
            patterns.append(p if p.startswith(r"\b") else re.escape(p.lower()))
    return any(re.search(p, t) for p in patterns)


def is_foreign_stack_title(title: str) -> bool:
    """True when title is clearly for Go/Java/etc. without your TS/Node or Python stack."""
    t = _norm(title)
    if re.search(
        r"\bnode\.?js\b|\bnodejs\b|\btypescript\b|\bnestjs\b|\bpython\b|\bfastapi\b",
        t,
    ):
        return False
    return any(re.search(p, t) for p in FOREIGN_STACK_TITLE)


def has_target_stack_signal(text: str) -> bool:
    """Keep jobs that mention your stacks: TypeScript/Node/NestJS and/or Python/FastAPI."""
    t = _norm(text)
    return bool(
        re.search(
            r"\btypescript\b|\bnestjs\b|\bnest\.?js\b|\bnode\.?js\b|\bnodejs\b|"
            r"\bpython\b|\bfastapi\b|\bdjango\b",
            t,
        )
    )


# Back-compat alias
def has_typescript_signal(text: str) -> bool:
    return has_target_stack_signal(text)


def has_title_include(title: str) -> bool:
    t = _norm(title)
    return any(re.search(p, t) for p in TITLE_INCLUDE)


def has_strong_title_signal(title: str) -> bool:
    t = _norm(title)
    return any(re.search(p, t) for p in STRONG_TITLE)


def should_ingest_title(title: str, extra_excludes: list[str] | None = None) -> bool:
    """Catalogue ingest is role-agnostic; only drop empty titles."""
    del extra_excludes  # kept for call-site back-compat
    return bool((title or "").strip())


def should_ingest_job(
    title: str,
    description: str = "",
    extra_excludes: list[str] | None = None,
    *,
    require_typescript: bool = False,
    require_target_stack: bool | None = None,
) -> bool:
    """Accept any job with a title — personal ranking happens later, not at ingest."""
    del description, require_typescript, require_target_stack
    return should_ingest_title(title, extra_excludes)


def skill_in_text(skill: str, text: str) -> bool:
    """Word-boundary-ish skill match; allow node.js / c++ style tokens."""
    s = _norm(skill)
    if len(s) < 2:
        return False
    # Escape then restore common tech dots
    if re.fullmatch(r"[a-z0-9.+#\-]+", s):
        pat = r"(?<![a-z0-9])" + re.escape(s).replace(r"\.", r"\.?") + r"(?![a-z0-9])"
        return re.search(pat, text) is not None
    return s in text
