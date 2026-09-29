"""Local-only request guard for the product API.

A server on 127.0.0.1 is still reachable from any web page the user visits, so loopback binding
alone is not enough:

- Host allowlist: blocks DNS rebinding (an attacker's domain resolving to 127.0.0.1 still sends
  its own name in the Host header).
- Session cookie (HttpOnly, SameSite=Strict), issued by GET /api/v1/session and required on every
  other /api route: a cross-site page's requests never carry it. EventSource can't send custom
  headers, which is why this is a cookie rather than a bearer token.
- Writes also need an `X-PDL-Request` header, which a cross-site form or simple request can't set
  without a CORS preflight this server never grants (there is no CORS middleware at all).
- A self-only Content-Security-Policy and no third-party anything.
"""

from __future__ import annotations

import hmac
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

SESSION_COOKIE = "pdl_session"
WRITE_HEADER = "x-pdl-request"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message, "details": {}, "retryable": False}},
        status_code=status,
    )


class LocalGuard(BaseHTTPMiddleware):
    def __init__(self, app, *, allowed_hosts: set[str], session_token: str | None = None):
        super().__init__(app)
        self.allowed_hosts = {h.lower() for h in allowed_hosts}
        self.session_token = session_token or secrets.token_urlsafe(32)

    async def dispatch(self, request: Request, call_next) -> Response:
        host = (request.headers.get("host") or "").lower()
        if host not in self.allowed_hosts:
            return _error(400, "bad_host", "unrecognized Host header")

        path = request.url.path
        if path.startswith("/api/"):
            if path == "/api/v1/session":
                response = JSONResponse({"ok": True})
                response.set_cookie(
                    SESSION_COOKIE, self.session_token, httponly=True, samesite="strict",
                    path="/",
                )
                return self._harden(response)
            cookie = request.cookies.get(SESSION_COOKIE, "")
            if not hmac.compare_digest(cookie, self.session_token):
                return self._harden(_error(401, "no_session", "session missing or expired"))
            if request.method not in _SAFE_METHODS and request.headers.get(WRITE_HEADER) != "1":
                return self._harden(_error(403, "missing_request_header", "write needs X-PDL-Request"))
        return self._harden(await call_next(request))

    @staticmethod
    def _harden(response: Response) -> Response:
        response.headers.setdefault("Content-Security-Policy", _CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        return response


def allowed_hosts_for(host: str, port: int) -> set[str]:
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
    if host not in ("127.0.0.1", "localhost", "::1"):
        hosts.add(f"{host}:{port}")
    return hosts
