from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from threading import Lock
from time import monotonic

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int


@dataclass(slots=True)
class _Window:
    started_at: float
    requests: int


class FixedWindowRateLimiter:
    """Small, thread-safe limiter suitable for one API process.

    Deployments running multiple processes should additionally enforce a shared limit at the
    reverse proxy or API gateway. This limiter still provides a local last line of defense.
    """

    def __init__(self, window_seconds: int) -> None:
        self.window_seconds = window_seconds
        self._windows: dict[str, _Window] = {}
        self._lock = Lock()
        self._requests_since_cleanup = 0

    def consume(self, key: str, limit: int, *, now: float | None = None) -> RateLimitResult:
        current_time = monotonic() if now is None else now
        with self._lock:
            self._requests_since_cleanup += 1
            if self._requests_since_cleanup >= 1000:
                cutoff = current_time - self.window_seconds
                self._windows = {
                    window_key: window
                    for window_key, window in self._windows.items()
                    if window.started_at > cutoff
                }
                self._requests_since_cleanup = 0

            window = self._windows.get(key)
            if window is None or current_time - window.started_at >= self.window_seconds:
                window = _Window(started_at=current_time, requests=0)
                self._windows[key] = window

            window.requests += 1
            remaining = max(limit - window.requests, 0)
            retry_after = max(1, ceil(self.window_seconds - (current_time - window.started_at)))
            return RateLimitResult(
                allowed=window.requests <= limit,
                limit=limit,
                remaining=remaining,
                retry_after=retry_after,
            )


def _request_identity(request: Request) -> str:
    # A raw bearer token is not trusted until authentication runs. Rate-limit by network
    # identity here so rotating invalid tokens cannot create unlimited buckets.
    client_host = request.client.host if request.client else "unknown"
    return f"ip:{client_host}"


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings
        self.limiter = FixedWindowRateLimiter(settings.rate_limit_window_seconds)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if (
            not self.settings.rate_limit_enabled
            or request.method in {"OPTIONS", "HEAD"}
            or request.url.path in {"/health", "/ready", "/docs", "/openapi.json", "/redoc"}
        ):
            return await call_next(request)

        is_write = request.method not in {"GET"}
        limit = (
            self.settings.rate_limit_write_requests
            if is_write
            else self.settings.rate_limit_read_requests
        )
        scope = "write" if is_write else "read"
        identity = _request_identity(request)
        result = self.limiter.consume(f"{identity}:{scope}", limit)
        headers = {
            "X-RateLimit-Limit": str(result.limit),
            "X-RateLimit-Remaining": str(result.remaining),
        }
        if not result.allowed:
            headers["Retry-After"] = str(result.retry_after)
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Try again later."},
                headers=headers,
            )

        response = await call_next(request)
        for header, value in headers.items():
            response.headers[header] = value
        return response
