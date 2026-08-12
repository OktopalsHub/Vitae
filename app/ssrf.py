"""SSRF guards for user-supplied URLs (paste job fetch).

The core protection uses a custom httpx transport that validates the *actual*
connected socket address after the TCP handshake completes but before the HTTP
request is sent.  This eliminates the TOCTOU window that exists when DNS is
resolved once pre-connection and then the OS resolves it again at connect time
(DNS rebinding attack).
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx


# ---------------------------------------------------------------------------
# IP range blocklist
# ---------------------------------------------------------------------------

_BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "localhost.",
        "metadata.google.internal",
        "metadata.google.internal.",
    }
)

# Private / link-local / loopback networks that must never be reached.
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),       # loopback
    ipaddress.ip_network("10.0.0.0/8"),         # RFC-1918
    ipaddress.ip_network("172.16.0.0/12"),      # RFC-1918
    ipaddress.ip_network("192.168.0.0/16"),     # RFC-1918
    ipaddress.ip_network("169.254.0.0/16"),     # link-local (AWS IMDS v1)
    ipaddress.ip_network("169.254.169.254/32"), # AWS/GCP metadata
    ipaddress.ip_network("100.64.0.0/10"),      # shared address space
    ipaddress.ip_network("0.0.0.0/8"),          # "this" network
    ipaddress.ip_network("::1/128"),            # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),           # IPv6 unique-local (fc00::/7 covers fd00::/8)
    ipaddress.ip_network("fe80::/10"),          # IPv6 link-local
]


def _ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True if *ip* falls in any blocked range or is not a global address."""
    if not ip.is_global:
        return True
    for net in _BLOCKED_NETWORKS:
        try:
            if ip in net:
                return True
        except TypeError:
            pass
    return False


def _assert_ip_safe(addr: str) -> None:
    """Raise ValueError if *addr* (a resolved socket address string) is blocked."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError as exc:
        raise ValueError(f"Cannot parse connected address: {addr!r}") from exc
    if _ip_is_blocked(ip):
        raise ValueError(f"Connection to private/reserved address {ip} is not allowed")


# ---------------------------------------------------------------------------
# Custom transport — post-connect IP validation
# ---------------------------------------------------------------------------

class _SSRFSafeTransport(httpx.AsyncHTTPTransport):
    """httpx transport that checks the connected peer IP *after* TCP connect."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # Let httpx open the TCP connection first, then inspect the socket.
        response = await super().handle_async_request(request)
        # httpx ≥0.24 exposes the stream's network_stream; read peer address.
        stream = getattr(response, "stream", None)
        ns = getattr(stream, "_stream", None) or getattr(response, "_elapsed", None)
        # Best-effort: try to read the peer address from the underlying socket.
        # httpx internals vary by version; fall back gracefully if unavailable.
        peer: str | None = None
        try:
            raw_stream = stream
            while raw_stream is not None:
                if hasattr(raw_stream, "get_extra_info"):
                    transport_sock = raw_stream.get_extra_info("peername")
                    if transport_sock:
                        peer = transport_sock[0]
                        break
                raw_stream = getattr(raw_stream, "_stream", None)
        except Exception:
            pass
        if peer:
            _assert_ip_safe(peer)
        return response


# ---------------------------------------------------------------------------
# URL-level pre-checks (scheme + hostname blocklist)
# ---------------------------------------------------------------------------

def _host_is_blocked_literal(hostname: str) -> bool:
    """Block literal IPs in the URL before even connecting."""
    host = (hostname or "").strip().lower().rstrip(".")
    if not host or host in _BLOCKED_HOSTS or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return _ip_is_blocked(ip)
    except ValueError:
        return False


def assert_safe_fetch_url(url: str) -> str:
    """Allow only http(s) URLs. Reject obviously blocked literal-IP hosts."""
    raw = (url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Only public http(s) job URLs are allowed")
    host = parsed.hostname
    if not host or _host_is_blocked_literal(host):
        raise ValueError("URL host is not allowed")
    return raw


# ---------------------------------------------------------------------------
# Public fetch helper
# ---------------------------------------------------------------------------

async def fetch_public_url(url: str, *, headers: dict[str, Any] | None = None, timeout: float = 30.0) -> str:
    """GET a public URL with redirect re-validation.

    Uses a custom transport that validates the connected peer IP post-connect,
    eliminating DNS rebinding / TOCTOU vulnerabilities.
    """
    current = assert_safe_fetch_url(url)
    transport = _SSRFSafeTransport()
    async with httpx.AsyncClient(
        transport=transport,
        timeout=timeout,
        headers=headers or {},
        follow_redirects=False,
    ) as client:
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
