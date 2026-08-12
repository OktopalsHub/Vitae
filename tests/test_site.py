"""Tests for public SEO and legal endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_robots_txt():
    resp = client.get("/robots.txt")
    assert resp.status_code == 200
    assert "Sitemap:" in resp.text
    assert "Disallow: /admin" in resp.text


def test_sitemap_xml():
    resp = client.get("/sitemap.xml")
    assert resp.status_code == 200
    assert "application/xml" in resp.headers.get("content-type", "")
    assert "<loc>" in resp.text
    assert "/about" in resp.text
    assert "/terms" in resp.text
    assert "/privacy" in resp.text


def test_llms_txt():
    resp = client.get("/llms.txt")
    assert resp.status_code == 200
    assert "Vitae" in resp.text
    assert "/about" in resp.text
    assert "/terms" in resp.text


def test_about_page():
    resp = client.get("/about")
    assert resp.status_code == 200
    assert "Job matching built" in resp.text
    assert 'rel="canonical"' in resp.text


def test_privacy_has_cookies_anchor():
    resp = client.get("/privacy")
    assert resp.status_code == 200
    assert 'id="cookies"' in resp.text


def test_terms_page():
    resp = client.get("/terms")
    assert resp.status_code == 200
    assert "Terms of Service" in resp.text
    assert 'rel="canonical"' in resp.text


def test_privacy_page():
    resp = client.get("/privacy")
    assert resp.status_code == 200
    assert "Privacy Policy" in resp.text


def test_home_has_favicon_and_canonical():
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'rel="icon"' in resp.text
    assert 'rel="canonical"' in resp.text
