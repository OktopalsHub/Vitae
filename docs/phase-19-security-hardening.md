# Phase 19: Security Hardening

## Goal

Strengthen the production HTTP boundary without changing application behavior or requiring a new infrastructure service.

## Changes

- [x] Add baseline browser security headers.
- [x] Enable HSTS only for production-like environments.
- [x] Bound incoming `X-Request-ID` values to prevent oversized request metadata.
- [x] Make trusted proxy addresses configurable.
- [x] Use a narrow localhost-only trusted proxy default instead of trusting every proxy.
- [x] Document `FORWARDED_ALLOW_IPS`.

## Production configuration

Set `FORWARDED_ALLOW_IPS` to the IP addresses or CIDRs of the actual reverse proxy/load balancer when deployed behind one. Do not leave a broad wildcard unless the network boundary guarantees that only the trusted proxy can reach the application.

Security headers include `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, and a restrictive `Permissions-Policy`. HSTS is added only when `APP_ENV` is production-like.

## Acceptance criteria

- [x] Local development continues to work without TLS.
- [x] Production responses receive baseline security headers.
- [x] Request IDs remain traceable but have a bounded size.
- [x] Proxy trust is explicit and configurable.
- [x] No new runtime infrastructure dependency is required.