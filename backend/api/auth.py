"""Minimal WebSocket auth + HTTP rate limiting.

Deliberately small — no external dependencies, no framework middleware
gymnastics. The intent is a sane default so a fresh deployment is not
wide open; for anything internet-facing, front this with a real auth
layer (an OAuth2 proxy, mTLS at the edge, a WAF, etc.).

Design
------
* **WS auth.** `require_ws_auth(ws)` checks the `?token=` query param
  against `settings.ws_auth_token`. When the setting is empty the
  check is disabled (dev mode) — logged once at startup. Auth failure
  closes the socket with 4401 before any subscription is registered.

* **HTTP rate limit.** `RateLimiter.check(key)` is a fixed-window
  in-memory counter: one bucket per minute per key (client IP).
  Simple, correct, and honest about being per-process — a multi-worker
  deploy should terminate at the reverse proxy instead.
"""
from __future__ import annotations

import hmac
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

from fastapi import HTTPException, Request, WebSocket, status

from ..config import settings

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# WebSocket auth
# ---------------------------------------------------------------------------

# Custom close codes (per RFC 6455 §7.4, 4000–4999 is for application use).
WS_CLOSE_UNAUTHORIZED = 4401


async def require_ws_auth(ws: WebSocket) -> bool:
    """Reject unauthenticated WebSocket connections. Returns True if OK.

    When `settings.ws_auth_token` is empty, auth is disabled and every
    connection is accepted (dev mode).
    """
    expected = settings.ws_auth_token
    if not expected:
        return True
    supplied = ws.query_params.get("token", "")
    # Constant-time compare so a timing side channel can't leak the token.
    if not hmac.compare_digest(supplied, expected):
        # Refuse BEFORE calling ws.accept() so the client sees a clean 403
        # rather than a mid-handshake close.
        await ws.close(code=WS_CLOSE_UNAUTHORIZED, reason="unauthorized")
        return False
    return True


# ---------------------------------------------------------------------------
# HTTP rate limiter
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Bucket:
    minute: int = 0        # unix minute this bucket represents
    count: int = 0


@dataclass(slots=True)
class RateLimiter:
    """Fixed-window per-key rate limiter, minute buckets, in-memory.

    Not a distributed limiter — for a multi-worker deploy do this at the
    reverse proxy. This is here so a single-worker deploy is not trivially
    DoS-able by a badly-behaved script.
    """

    per_minute: int
    _buckets: dict[str, _Bucket] = field(default_factory=lambda: defaultdict(_Bucket))

    def check(self, key: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds). No throttling if per_minute==0."""
        if self.per_minute <= 0:
            return True, 0
        now_min = int(time.time() // 60)
        b = self._buckets[key]
        if b.minute != now_min:
            b.minute = now_min
            b.count = 0
        b.count += 1
        if b.count > self.per_minute:
            retry_after = 60 - int(time.time() % 60)
            return False, retry_after
        return True, 0


_limiter = RateLimiter(per_minute=settings.http_rate_limit_per_minute)


def _client_key(request: Request) -> str:
    """Best-effort client identifier. Trusts X-Forwarded-For when present
    (uvicorn is started with --proxy-headers in the container)."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


async def rate_limit(request: Request) -> None:
    """FastAPI dependency: raises 429 with Retry-After when a client
    exceeds the per-minute quota. No-op when the setting is 0."""
    key = _client_key(request)
    ok, retry = _limiter.check(key)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error": "rate_limited", "retry_after_seconds": retry},
            headers={"Retry-After": str(retry)},
        )
