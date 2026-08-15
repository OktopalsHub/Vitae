"""Public SEO, legal, and crawler endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.config import project_path, site_base_url
from app.web_helpers import template_ctx

router = APIRouter(tags=["site"])
templates = Jinja2Templates(directory=str(project_path("app", "templates")))


@router.get("/favicon.ico")
def favicon_ico() -> RedirectResponse:
    return RedirectResponse(url="/static/favicon.svg", status_code=301)

_PUBLIC_PATHS = (
    "/",
    "/about",
    "/login",
    "/register",
    "/terms",
    "/privacy",
)


def _absolute(path: str) -> str:
    return f"{site_base_url()}{path}"


@router.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt() -> str:
    base = site_base_url()
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /admin\n"
        "Disallow: /jobs\n"
        "Disallow: /settings\n"
        "Disallow: /onboarding\n"
        "Disallow: /profiles\n"
        "Disallow: /paste\n"
        "Disallow: /auth/\n"
        f"Sitemap: {base}/sitemap.xml\n"
    )


@router.get("/sitemap.xml")
def sitemap_xml() -> Response:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    urls = "\n".join(
        f"  <url><loc>{_absolute(path)}</loc><lastmod>{today}</lastmod></url>"
        for path in _PUBLIC_PATHS
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}\n"
        "</urlset>\n"
    )
    return Response(content=body, media_type="application/xml")


@router.get("/llms.txt", response_class=PlainTextResponse)
def llms_txt() -> str:
    base = site_base_url()
    return f"""# Vitae

> Personal job matching — score open roles against your CV and apply with tailored resumes.

Vitae is a web application for job seekers. Users upload a CV, browse scored job listings,
and generate application materials (tailored resume, cover note, form answers) with optional AI.

## Public pages

- Home: {base}/
- Sign in: {base}/login
- Register: {base}/register
- Terms of Service: {base}/terms
- Privacy Policy: {base}/privacy
- About: {base}/about

## Product (requires account)

- Job board with match scores: {base}/jobs
- Billing / subscriptions: {base}/billing

## Optional

- Full documentation for authenticated features is not public.
- Job listings are aggregated from third-party boards; Vitae does not guarantee listing accuracy.
"""


@router.get("/about", response_class=HTMLResponse)
def about_page(request: Request):
    return templates.TemplateResponse(
        request,
        "legal/about.html",
        template_ctx(request, None),
    )


@router.get("/terms", response_class=HTMLResponse)
def terms_page(request: Request):
    return templates.TemplateResponse(
        request,
        "legal/terms.html",
        template_ctx(request, None),
    )


@router.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request):
    return templates.TemplateResponse(
        request,
        "legal/privacy.html",
        template_ctx(request, None),
    )
