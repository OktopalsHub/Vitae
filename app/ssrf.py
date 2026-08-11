"""SSRF guards for user-supplied URLs (paste job fetch)."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse


_BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "localhost.",
        "metadata.google.internal",
        "metadata.google.internal.",
    }
)


def _host_resolves_public(hostname: str) -> bool:
    host = (hostname or "").strip().lower().rstrip(".")
    if not host or host in _BLOCKED_HOSTS or host.endswith(".localhost"):
        return False
    try:
        # Literal IP in hostname
        ip = ipaddress.ip_address(host)
        return bool(ip.is_global)
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        if not ip.is_global:
            return False
    return True


def assert_safe_fetch_url(url: str) -> str:
    """Allow only http(s) URLs whose DNS resolves to public IPs."""
    raw = (url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Only public http(s) job URLs are allowed")
    host = parsed.hostname
    if not host or not _host_resolves_public(host):
        raise ValueError("URL host is not allowed")
    return raw


async def fetch_public_url(url: str, *, headers: dict | None = None, timeout: float = 30.0) -> str:
    """GET a public URL with redirect re-validation (blocks SSRF to private ranges)."""
    import httpx

    current = assert_safe_fetch_url(url)
    async with httpx.AsyncClient(timeout=timeout, headers=headers or {}, follow_redirects=False) as client:
        for _ in range(4):
            resp = await client.get(current)
            if resp.is_redirect:
                loc = resp.headers.get("location") or ""
                if not loc:
                    raise ValueError("Redirect without location")
                nxt = urljoin(str(resp.url), loc)
                current = assert_safe_fetch_url(nxt)
                continue
            resp.raise_for_status()
            return resp.text
    raise ValueError("Too many redirects")
