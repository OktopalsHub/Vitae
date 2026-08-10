from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from fpdf import FPDF
from slugify import slugify

from app.config import project_path
from app.llm import LLMCreds, has_llm, llm_complete
from app.profile.loader import load_or_build_profile

JobLike = Any


def _to_ascii(text: str) -> str:
    return (
        (text or "")
        .replace("—", "-")
        .replace("–", "-")
        .replace("’", "'")
        .replace("“", '"')
        .replace("”", '"')
    )


SYSTEM_PROMPT = """You are an expert resume writer using ASD-STE100 Simplified Technical English.
Rewrite the candidate's resume for ONE specific job application.

Rules:
- Use short, active sentences. One idea per bullet.
- Mirror important job keywords naturally for ATS (do not keyword-stuff).
- Keep all facts truthful. Do not invent employers, titles, or years.
- NEVER include GeoIP, PEP, age verification, or KYC compliance bullets unless the job explicitly requires geo-restriction or KYC systems.
- Prefer Node.js / TypeScript / REST / databases / Docker / cloud evidence from the profile.
- Target a concise one-page resume.
- Return ONLY valid JSON matching the schema below. No markdown fences.

JSON schema:
{
  "summary": "string",
  "skills": [{"label": "string", "value": "string"}],
  "experiences": [{"title": "string", "dates": "string", "bullets": ["string"]}],
  "projects": [{"title": "string", "dates": "string", "bullets": ["string"]}],
  "education": [{"school": "string", "dates": "string", "details": "string"}]
}
"""


async def _llm_complete(prompt: str, creds: LLMCreds | None = None) -> str:
    return await llm_complete(
        prompt=prompt,
        system=SYSTEM_PROMPT,
        json_mode=True,
        max_tokens=4000,
        temperature=0.3,
        creds=creds,
    )


_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
_DATE_SPAN = re.compile(
    rf"("
    rf"{_MONTH}\s+\d{{4}}\s*[-–—]\s*(?:{_MONTH}\s*[-–—]?\s*\d{{4}}|Present|\d{{4}})"
    rf"|"
    rf"{_MONTH}\s*[-–—]\s*\d{{4}}\s*[-–—]\s*(?:{_MONTH}\s*[-–—]?\s*\d{{4}}|Present|\d{{4}})"
    rf"|"
    rf"\d{{4}}\s*[-–—]\s*(?:Present|\d{{4}})"
    rf")",
    re.I,
)


def _dedupe_csv(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = re.sub(r"\s+", " ", item).strip(" ,;")
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def _split_header(line: str) -> tuple[str, str]:
    """Split 'Company - Role   dates' into title + dates."""
    line = re.sub(r"\s+", " ", line).strip()
    # Normalize "May - 2024" -> "May 2024"
    line = re.sub(rf"({_MONTH})\s*[-–—]\s*(\d{{4}})", r"\1 \2", line, flags=re.I)
    m = _DATE_SPAN.search(line)
    dates = ""
    title = line
    if m:
        dates = re.sub(r"\s*[-–—]\s*", " - ", m.group(0))
        dates = re.sub(r"\s*-\s*-\s*", " - ", dates)
        title = (line[: m.start()] + " " + line[m.end() :]).strip(" -|\t")
    title = re.sub(r"\s{2,}", " ", title).strip(" -|")
    return title, dates


def _ste100_bullet(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    # Prefer simpler starts
    replacements = [
        (r"^Architected and delivered\b", "Design and deliver"),
        (r"^Architected and integrated\b", "Add"),
        (r"^Architected\b", "Design"),
        (r"^Engineered\b", "Build"),
        (r"^Developed\b", "Build"),
        (r"^Implemented\b", "Implement"),
        (r"^Maintaining\b", "Maintain"),
        (r"^Enhanced\b", "Improve"),
        (r"^Optimized\b", "Optimize"),
        (r"^Authored and published\b", "Publish"),
        (r"^Authored\b", "Write"),
        (r"^Contributed to defining\b", "Define"),
        (r"^Led the backend development team for\b", "Lead backend development for"),
        (r"^Collaborated with\b", "Work with"),
        (r"^Integrated\b", "Integrate"),
        (r"^Secured\b", "Secure"),
        (r"^Automated\b", "Automate"),
        (r"^Deployed\b", "Deploy"),
    ]
    for pat, rep in replacements:
        t = re.sub(pat, rep, t, count=1, flags=re.I)
    # Truncate very long bullets
    if len(t) > 220:
        t = t[:217].rsplit(" ", 1)[0] + "."
    return t


def _is_excluded_bullet(line: str) -> bool:
    lower = line.lower()
    banned = (
        "geoip",
        "politically exposed",
        "pep (",
        "age verification",
        "kyc-linked",
        "kyc ",
    )
    return any(b in lower for b in banned)


def _is_blockchain_heavy(line: str) -> bool:
    lower = line.lower()
    return any(
        k in lower
        for k in ("smart contract", "solidity", "blockchain", "on-chain", "cross-chain", "defi", "token transfer")
    )


def _jd_wants_blockchain(job: JobLike) -> bool:
    blob = f"{job.title} {job.description}".lower()
    return any(k in blob for k in ("blockchain", "solidity", "web3", "smart contract", "crypto"))


def _jd_keywords(job: JobLike) -> list[str]:
    blob = f"{job.title} {job.description}".lower()
    catalog = [
        "Node.js",
        "TypeScript",
        "JavaScript",
        "NestJS",
        "Express",
        "REST",
        "MongoDB",
        "PostgreSQL",
        "MySQL",
        "Redis",
        "Docker",
        "AWS",
        "payment",
        "API",
        "JWT",
    ]
    hits = [k for k in catalog if k.lower().replace(".js", "") in blob or k.lower() in blob]
    return hits[:8]


def _parse_blocks(lines: list[str], role_hints: tuple[str, ...]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in lines:
        low = line.lower()
        looks_header = _DATE_SPAN.search(line) and any(h in low for h in role_hints)
        if looks_header:
            if current:
                blocks.append(current)
            title, dates = _split_header(line)
            current = {"title": title, "dates": dates, "bullets": []}
        elif current and line:
            if _is_excluded_bullet(line):
                continue
            current["bullets"].append(line)
    if current:
        blocks.append(current)
    return blocks


def _fallback_resume(profile: dict[str, Any], job: JobLike) -> dict[str, Any]:
    """Rule-based tailor when LLM is unavailable — still usable."""
    skills = profile.get("skills") or []

    def pick(keys: tuple[str, ...]) -> str:
        chosen = []
        for s in skills:
            low = s.lower()
            if any(k in low for k in keys):
                # Flatten nested "TypeScript (Node.js, NestJS)"
                parts = re.split(r"[,;/()]| and ", s)
                chosen.extend(p.strip() for p in parts if p.strip())
        # Prefer canonical stack for ATS
        canonical = [
            "Node.js",
            "TypeScript",
            "JavaScript",
            "NestJS",
            "Express.js",
            "Python",
            "FastAPI",
            "MySQL",
            "PostgreSQL",
            "MongoDB",
            "Redis",
            "TypeORM",
            "Prisma",
            "Mongoose",
            "Docker",
            "AWS",
            "Git",
            "CI/CD",
        ]
        merged = _dedupe_csv(chosen + [c for c in canonical if any(k in c.lower() for k in keys)])
        # Keep only items matching keys
        merged = [m for m in merged if any(k in m.lower() for k in keys)]
        return ", ".join(merged[:8]) if merged else ""

    skill_groups = [
        {
            "label": "Languages & Frameworks",
            "value": pick(("node", "type", "javascript", "nest", "express", "python", "fast"))
            or "Node.js, TypeScript, JavaScript, NestJS, Express.js",
        },
        {
            "label": "Databases",
            "value": pick(("sql", "mongo", "redis", "prisma", "typeorm", "mongoose", "postgres", "mysql"))
            or "MySQL, PostgreSQL, MongoDB, Redis",
        },
        {
            "label": "DevOps & Tools",
            "value": pick(("docker", "aws", "git", "ci", "cloud", "digitalocean"))
            or "Docker, AWS, Git, CI/CD",
        },
        {
            "label": "APIs & Systems",
            "value": "RESTful APIs, WebSockets, multi-tenant SaaS, microservices",
        },
    ]
    # Ensure JD keywords appear in skills text when relevant
    jd_kw = _jd_keywords(job)
    if jd_kw:
        skill_groups[0]["value"] = ", ".join(
            _dedupe_csv(skill_groups[0]["value"].split(",") + [k for k in jd_kw if k in (
                "Node.js", "TypeScript", "JavaScript", "NestJS", "Express", "REST", "API", "JWT"
            )])
        )

    keep_chain = _jd_wants_blockchain(job)
    experiences = _parse_blocks(
        profile.get("experience_raw") or [],
        ("engineer", "developer", "author"),
    )
    for exp in experiences:
        bullets = []
        for b in exp.get("bullets") or []:
            if not keep_chain and _is_blockchain_heavy(b):
                continue
            bullets.append(_ste100_bullet(b))
        # Prefer payment/API/docker bullets when JD mentions them
        jd_blob = f"{job.title} {job.description}".lower()
        if "payment" in jd_blob or "fintech" in jd_blob:
            bullets.sort(key=lambda x: 0 if "payment" in x.lower() or "subscription" in x.lower() else 1)
        exp["bullets"] = bullets[:5]
        # Clean role title: drop trailing junk
        exp["title"] = re.sub(r"\s*\|?\s*$", "", exp.get("title") or "").strip()
        # Normalize Cashflakes blockchain title for non-web3 jobs
        if not keep_chain and "blockchain" in exp["title"].lower():
            exp["title"] = re.sub(r"/?\s*Blockchain\s*", "", exp["title"], flags=re.I).strip(" -/")
            if "developer" not in exp["title"].lower() and "engineer" not in exp["title"].lower():
                exp["title"] = exp["title"] + " - Backend Developer"

    experiences = [e for e in experiences if e.get("bullets")][:4]

    projects = _parse_blocks(
        profile.get("projects_raw") or [],
        ("engineer", "developer", "author", "open source"),
    )
    for proj in projects:
        proj["title"] = re.sub(r"\s*\|?\s*$", "", proj.get("title") or "").strip()
        proj["bullets"] = [_ste100_bullet(b) for b in (proj.get("bullets") or []) if not _is_excluded_bullet(b)][:2]
    projects = [p for p in projects if p.get("bullets")][:2]

    edu_lines = profile.get("education_raw") or []
    education = []
    if edu_lines:
        school_line = edu_lines[0]
        school, dates = _split_header(school_line)
        details = edu_lines[1] if len(edu_lines) > 1 else "Master of Science in Information Technology (MIT)"
        if not dates:
            m = re.search(rf"{_MONTH}.+", school_line, re.I)
            if m:
                dates = m.group(0).strip()
                school = school_line[: m.start()].strip()
        education = [{"school": school or "Miva Open University", "dates": dates or "Jan 2026 - Present", "details": details}]

    focus = ", ".join(jd_kw[:5]) if jd_kw else "Node.js and TypeScript"
    company = job.company or "this team"
    summary = (
        f"Backend Engineer with more than 4 years of experience in Node.js and TypeScript. "
        f"I design, build, and maintain scalable server-side applications and RESTful APIs. "
        f"I work with {focus}. "
        f"I optimize services for speed, reliability, and secure production systems for roles like "
        f"{job.title} at {company}."
    )

    return {
        "summary": summary,
        "skills": skill_groups,
        "experiences": experiences,
        "projects": projects,
        "education": education
        or [
            {
                "school": "Miva Open University",
                "dates": "Jan 2026 - Present",
                "details": "Master of Science in Information Technology (MIT)",
            }
        ],
    }


def _post_validate_resume(data: dict[str, Any], job: JobLike) -> dict[str, Any]:
    """Normalize LLM or fallback output."""
    if not _jd_wants_blockchain(job):
        for exp in data.get("experiences") or []:
            exp["bullets"] = [
                b for b in (exp.get("bullets") or []) if not _is_blockchain_heavy(b) and not _is_excluded_bullet(b)
            ]
            if "blockchain" in (exp.get("title") or "").lower():
                exp["title"] = re.sub(r"/?\s*Blockchain\s*", "", exp["title"], flags=re.I).strip(" -/")
        for proj in data.get("projects") or []:
            proj["bullets"] = [
                b for b in (proj.get("bullets") or []) if not _is_excluded_bullet(b)
            ]
    for sk in data.get("skills") or []:
        if isinstance(sk.get("value"), str):
            parts = [p.strip() for p in sk["value"].split(",")]
            sk["value"] = ", ".join(_dedupe_csv(parts))
    for exp in data.get("experiences") or []:
        title, dates = exp.get("title") or "", exp.get("dates") or ""
        if not dates and _DATE_SPAN.search(title):
            t2, d2 = _split_header(title)
            exp["title"], exp["dates"] = t2, d2
        exp["title"] = re.sub(r"\s*\|\s*$", "", exp.get("title") or "").strip()
        exp["dates"] = re.sub(r"\s*-\s*-\s*", " - ", exp.get("dates") or "").strip(" |")
    return data


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


async def build_tailored_content(
    job: JobLike,
    profile: dict[str, Any] | None = None,
    creds: LLMCreds | None = None,
) -> dict[str, Any]:
    profile = profile or load_or_build_profile()
    if not has_llm(creds):
        return _post_validate_resume(_fallback_resume(profile, job), job)

    prompt = f"""Candidate profile JSON:
{json.dumps({k: profile[k] for k in ('name','contact','summary','skills','experience_raw','projects_raw','education_raw') if k in profile}, indent=2)[:14000]}

Target job:
Title: {job.title}
Company: {job.company}
Location: {job.location}
URL: {job.url}
Description:
{(job.description or '')[:7000]}

Create a tailored resume JSON for this job.
"""
    try:
        raw = await _llm_complete(prompt, creds=creds)
        data = _extract_json(raw)
        # Minimal validation
        if not data.get("summary") or not data.get("experiences"):
            raise ValueError("Incomplete LLM resume")
        return _post_validate_resume(data, job)
    except Exception:
        return _post_validate_resume(_fallback_resume(profile, job), job)


def resume_to_markdown(name: str, contact: str, data: dict[str, Any]) -> str:
    lines = [f"# {name}", "", contact, "", "## Professional Summary", "", data.get("summary", ""), "", "## Skills", ""]
    for sk in data.get("skills") or []:
        lines.append(f"- **{sk.get('label', 'Skills')}:** {sk.get('value', '')}")
    lines += ["", "## Professional Experience", ""]
    for exp in data.get("experiences") or []:
        lines.append(f"### {exp.get('title', '')} | {exp.get('dates', '')}")
        lines.append("")
        for b in exp.get("bullets") or []:
            lines.append(f"- {b}")
        lines.append("")
    if data.get("projects"):
        lines += ["## Projects", ""]
        for proj in data["projects"]:
            lines.append(f"### {proj.get('title', '')} | {proj.get('dates', '')}")
            lines.append("")
            for b in proj.get("bullets") or []:
                lines.append(f"- {b}")
            lines.append("")
    lines += ["## Education", ""]
    for edu in data.get("education") or []:
        lines.append(f"**{edu.get('school', '')}** | {edu.get('dates', '')}  ")
        lines.append(edu.get("details", ""))
        lines.append("")
    return "\n".join(lines)


def _add_right_tab(paragraph, pos: str = "9360") -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    tabs = OxmlElement("w:tabs")
    tab = OxmlElement("w:tab")
    tab.set(qn("w:val"), "right")
    tab.set(qn("w:pos"), pos)
    tabs.append(tab)
    p_pr.append(tabs)


def write_docx(path: Path, name: str, contact: str, data: dict[str, Any]) -> None:
    doc = Document()
    for section in doc.sections:
        section.top_margin = Inches(0.55)
        section.bottom_margin = Inches(0.55)
        section.left_margin = Inches(0.7)
        section.right_margin = Inches(0.7)

    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.5)

    def heading(text: str) -> None:
        p = doc.add_paragraph()
        run = p.add_run(text.upper())
        run.bold = True
        run.font.size = Pt(11)
        run.font.color.rgb = RGBColor(0x1A, 0x1A, 0x1A)
        p.paragraph_format.space_before = Pt(10)
        p.paragraph_format.space_after = Pt(3)

    def job_header(title: str, dates: str) -> None:
        p = doc.add_paragraph()
        r = p.add_run(title)
        r.bold = True
        r.font.size = Pt(10.5)
        p.add_run("\t" + dates)
        p.paragraph_format.space_before = Pt(6)
        p.paragraph_format.space_after = Pt(1)
        _add_right_tab(p)

    name_p = doc.add_paragraph()
    name_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    nr = name_p.add_run(name)
    nr.bold = True
    nr.font.size = Pt(18)

    ct = doc.add_paragraph()
    ct.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cr = ct.add_run(contact)
    cr.font.size = Pt(9.5)

    heading("Professional Summary")
    doc.add_paragraph(data.get("summary", ""))

    heading("Skills")
    for sk in data.get("skills") or []:
        p = doc.add_paragraph()
        r = p.add_run(f"{sk.get('label', 'Skills')}: ")
        r.bold = True
        p.add_run(sk.get("value", ""))

    heading("Professional Experience")
    for exp in data.get("experiences") or []:
        job_header(exp.get("title", ""), exp.get("dates", ""))
        for b in exp.get("bullets") or []:
            bp = doc.add_paragraph(b, style="List Bullet")
            bp.paragraph_format.space_after = Pt(1)

    if data.get("projects"):
        heading("Projects")
        for proj in data["projects"]:
            job_header(proj.get("title", ""), proj.get("dates", ""))
            for b in proj.get("bullets") or []:
                bp = doc.add_paragraph(b, style="List Bullet")
                bp.paragraph_format.space_after = Pt(1)

    heading("Education")
    for edu in data.get("education") or []:
        job_header(edu.get("school", ""), edu.get("dates", ""))
        doc.add_paragraph(edu.get("details", ""))

    doc.save(str(path))


class ResumePDF(FPDF):
    def section_title(self, title: str) -> None:
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(26, 26, 26)
        self.ln(3)
        self.cell(0, 6, title.upper(), new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(40, 40, 40)
        self.set_line_width(0.4)
        y = self.get_y()
        self.line(self.l_margin, y, self.w - self.r_margin, y)
        self.ln(2)

    def job_header(self, title: str, dates: str) -> None:
        self.set_font("Helvetica", "B", 10)
        page_width = self.w - self.l_margin - self.r_margin
        self.cell(page_width * 0.72, 5, _to_ascii(title), align="L")
        self.set_font("Helvetica", "", 9.5)
        self.cell(page_width * 0.28, 5, _to_ascii(dates), align="R", new_x="LMARGIN", new_y="NEXT")

    def bullet(self, text: str) -> None:
        self.set_font("Helvetica", "", 9.5)
        x = self.l_margin
        self.set_x(x)
        self.cell(4, 4.3, "-")
        self.multi_cell(self.w - self.r_margin - x - 4, 4.3, _to_ascii(text))
        self.ln(0.3)


def write_pdf(path: Path, name: str, contact: str, data: dict[str, Any]) -> None:
    pdf = ResumePDF(format="Letter")
    pdf.set_auto_page_break(auto=True, margin=0.45 * 25.4)
    pdf.set_margins(0.6 * 25.4, 0.45 * 25.4, 0.6 * 25.4)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 8, _to_ascii(name), align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, _to_ascii(contact), align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

    pdf.section_title("Professional Summary")
    pdf.set_font("Helvetica", "", 9.5)
    pdf.multi_cell(0, 4.4, _to_ascii(data.get("summary", "")))

    pdf.section_title("Skills")
    for sk in data.get("skills") or []:
        pdf.set_font("Helvetica", "B", 9.5)
        pdf.write(4.3, f"{sk.get('label', 'Skills')}: ")
        pdf.set_font("Helvetica", "", 9.5)
        pdf.write(4.3, _to_ascii(sk.get("value", "")))
        pdf.ln(4.5)

    pdf.section_title("Professional Experience")
    for exp in data.get("experiences") or []:
        pdf.ln(1)
        pdf.job_header(exp.get("title", ""), exp.get("dates", ""))
        for b in exp.get("bullets") or []:
            pdf.bullet(b)

    if data.get("projects"):
        pdf.section_title("Projects")
        for proj in data["projects"]:
            pdf.ln(1)
            pdf.job_header(proj.get("title", ""), proj.get("dates", ""))
            for b in proj.get("bullets") or []:
                pdf.bullet(b)

    pdf.section_title("Education")
    for edu in data.get("education") or []:
        pdf.ln(1)
        pdf.job_header(edu.get("school", ""), edu.get("dates", ""))
        pdf.set_font("Helvetica", "", 9.5)
        pdf.multi_cell(0, 4.3, _to_ascii(edu.get("details", "")))

    pdf.output(str(path))


def output_dir_for(
    job: JobLike,
    user_id: str | None = None,
    *,
    profile_id: int | None = None,
) -> Path:
    company = slugify(job.company or "company")[:40] or "company"
    role = slugify(job.title or "role")[:50] or "role"
    if not user_id:
        raise ValueError("user_id is required for resume output paths")
    if profile_id is not None:
        folder = project_path(
            "data",
            "users",
            str(user_id),
            "profiles",
            str(profile_id),
            "outputs",
            f"{job.id}-{company}-{role}",
        )
    else:
        folder = project_path(
            "data", "users", str(user_id), "outputs", f"{job.id}-{company}-{role}"
        )
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _safe_filename_part(value: str, fallback: str, max_len: int = 80) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", (value or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return (cleaned[:max_len] or fallback).rstrip(" .")


def resume_basename(job: JobLike, display_name: str | None = None) -> str:
    """Human download name: Name - Role - Company."""
    name = _safe_filename_part(display_name or "Candidate", "Candidate")
    role = _safe_filename_part(job.title or "", "Role")
    company = _safe_filename_part(job.company or "", "Company")
    return f"{name} - {role} - {company}"


def list_resume_files(output_dir: str | Path | None) -> list[str]:
    if not output_dir:
        return []
    out = Path(output_dir)
    if not out.exists():
        return []
    return sorted(
        p.name
        for p in out.iterdir()
        if p.is_file() and p.suffix.lower() in {".pdf", ".docx"}
    )


async def generate_resume_files(
    job: JobLike,
    *,
    profile: dict[str, Any] | None = None,
    creds: LLMCreds | None = None,
    user_id: str | None = None,
    profile_id: int | None = None,
    display_name: str | None = None,
) -> Path:
    profile = profile or load_or_build_profile()
    name = display_name or profile.get("name") or "Candidate"
    contact = profile.get("contact") or ""
    data = await build_tailored_content(job, profile=profile, creds=creds)
    out = output_dir_for(job, user_id=user_id, profile_id=profile_id)

    # Clear prior exports so only current PDF/DOCX remain listed.
    for old in out.iterdir():
        if old.is_file():
            old.unlink(missing_ok=True)

    base = resume_basename(job, display_name=name)
    write_docx(out / f"{base}.docx", name, contact, data)
    write_pdf(out / f"{base}.pdf", name, contact, data)
    return out
