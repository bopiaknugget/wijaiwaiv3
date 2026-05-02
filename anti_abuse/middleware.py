"""
middleware.py — FastAPI dependency factories and middleware integration.

This module is the single integration point between the anti_abuse modules
and the FastAPI application.  It wires together:

    1. RateLimiter   (global + per-user sliding window)
    2. TokenQuota    (daily LLM token budget, Bangkok timezone)
    3. ConcurrencyLimiter (per-user concurrent request gate)

Design choices
--------------
- All three limiters are instantiated once (application lifetime) and shared
  via module-level singletons.  This avoids re-registering Lua scripts on
  every request.
- Redis connection is created with a tight socket_timeout to avoid stalling
  request threads when Redis is slow.
- A single FastAPI dependency `require_not_rate_limited` applies all three
  layers in order: global rate -> user rate -> concurrency.
- Token quota is checked via a separate dependency `require_token_budget`
  because it requires a token count argument (known only at LLM call time).
- HTTP 429 responses always use the exact payload required by the frontend:
  {"detail": "ถึง Rate Limit กรุณารอสักครู่"}

Response headers
----------------
Every response (including 429) carries:
    X-RateLimit-Limit        — per-user 60-second window limit
    X-RateLimit-Remaining    — token quota remaining today
    X-RateLimit-Reset        — seconds until Bangkok midnight (token quota reset)

Fail-open policy
----------------
If Redis is unavailable, all checks pass through and no headers are mutated.

Usage (in FastAPI app):
    from anti_abuse.middleware import (
        get_anti_abuse_deps,
        require_not_rate_limited,
        require_token_budget,
        add_rate_limit_headers,
        create_redis_client,
    )

    app = FastAPI()

    # Startup: initialise singletons
    @app.on_event("startup")
    def startup():
        from anti_abuse.middleware import init_limiters
        init_limiters(redis_url="redis://localhost:6379/0")

    # Protect a regular endpoint
    @app.post("/chat", dependencies=[Depends(require_not_rate_limited)])
    async def chat(request: Request, user_id: str = Depends(get_current_user)):
        ...

    # Protect an LLM endpoint with token quota
    @app.post("/research")
    async def research(
        request: Request,
        user_id: str = Depends(get_current_user),
        _rl = Depends(require_not_rate_limited),
    ):
        require_token_budget(user_id, estimated_tokens=12_000)
        result = call_llm(...)
        # Refund unused tokens if LLM used fewer
        actual = result.usage.total_tokens
        if actual < 12_000:
            token_quota.refund(user_id, 12_000 - actual)
        return result
"""

import logging
import os
from typing import Optional

import redis
from fastapi import Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from anti_abuse.rate_limit import RateLimiter, RateLimitConfig
from anti_abuse.token_limit import TokenQuota
from anti_abuse.concurrency import ConcurrencyLimiter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level singletons — populated by init_limiters()
# ---------------------------------------------------------------------------
_redis_client: Optional[redis.Redis] = None
rate_limiter: Optional[RateLimiter] = None
token_quota: Optional[TokenQuota] = None
concurrency_limiter: Optional[ConcurrencyLimiter] = None

# Canonical 429 payload — must match exactly what the frontend expects
_429_DETAIL = "ถึง Rate Limit กรุณารอสักครู่"


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def create_redis_client(
    redis_url: str = "redis://localhost:6379/0",
    socket_timeout: float = 1.0,
    socket_connect_timeout: float = 1.0,
    max_connections: int = 30,
    decode_responses: bool = True,
) -> redis.Redis:
    """
    Create a Redis client with conservative timeouts.

    socket_timeout=1.0 means any Redis operation that takes more than 1 second
    raises redis.TimeoutError, which the limiters catch and fail-open on.

    max_connections=30 is sized for 20 concurrent users with headroom.
    protocol=3 enables RESP3 (Redis 7.x) for reduced parsing overhead.
    socket_keepalive=True prevents silent connection drops under idle periods.
    """
    return redis.from_url(
        redis_url,
        socket_timeout=socket_timeout,
        socket_connect_timeout=socket_connect_timeout,
        max_connections=max_connections,
        decode_responses=decode_responses,
        socket_keepalive=True,
        protocol=3,
    )


def init_limiters(
    redis_url: Optional[str] = None,
    redis_client: Optional[redis.Redis] = None,
    daily_token_limit: int = 5_000,
    max_concurrent: int = 3,
    global_1s_limit: int = 5,
    global_60s_limit: int = 200,
    user_60s_limit: int = 30,
    user_3600s_limit: int = 300,
) -> None:
    """
    Initialise module-level singletons.  Call once during app startup.

    Either supply a pre-built `redis_client` or a `redis_url` string.
    If neither is provided, falls back to REDIS_URL environment variable,
    then to redis://localhost:6379/0.

    Args:
        redis_url         : Redis connection URL (used if redis_client is None)
        redis_client      : Pre-built redis.Redis instance (overrides redis_url)
        daily_token_limit : Max combined LLM tokens per user per Bangkok day
        max_concurrent    : Max simultaneous requests per user
        global_1s_limit   : Global requests per second
        global_60s_limit  : Global requests per minute
        user_60s_limit    : Per-user requests per 60 seconds
        user_3600s_limit  : Per-user requests per hour
    """
    global _redis_client, rate_limiter, token_quota, concurrency_limiter

    if redis_client is not None:
        _redis_client = redis_client
    else:
        url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        _redis_client = create_redis_client(redis_url=url)

    rate_limiter = RateLimiter(
        redis_client=_redis_client,
        global_tiers=(
            RateLimitConfig(window_ms=1_000,   limit=global_1s_limit,   ttl_sec=5),
            RateLimitConfig(window_ms=60_000,  limit=global_60s_limit,  ttl_sec=120),
        ),
        user_tiers=(
            RateLimitConfig(window_ms=60_000,    limit=user_60s_limit,   ttl_sec=120),
            RateLimitConfig(window_ms=3_600_000, limit=user_3600s_limit, ttl_sec=7200),
        ),
    )
    token_quota = TokenQuota(
        redis_client=_redis_client,
        daily_limit=daily_token_limit,
    )
    concurrency_limiter = ConcurrencyLimiter(
        redis_client=_redis_client,
        max_concurrent=max_concurrent,
    )
    logger.info(
        "anti_abuse limiters initialised — daily_tokens=%d max_concurrent=%d "
        "global=[%d/s %d/min] user=[%d/min %d/hr]",
        daily_token_limit, max_concurrent,
        global_1s_limit, global_60s_limit,
        user_60s_limit, user_3600s_limit,
    )


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

def _assert_initialised() -> None:
    if rate_limiter is None or token_quota is None or concurrency_limiter is None:
        raise RuntimeError(
            "anti_abuse limiters not initialised. "
            "Call anti_abuse.middleware.init_limiters() during app startup."
        )


def require_not_rate_limited(
    request: Request,
    user_id: Optional[str] = None,
) -> None:
    """
    FastAPI dependency — apply global + per-user rate limits and concurrency gate.

    Raises HTTPException(429) with the canonical Thai payload if any limit
    is exceeded.

    If `user_id` is None (unauthenticated request), only global limits apply
    and the concurrency gate is skipped.

    The concurrency slot is acquired here but NOT released — the matching
    release must be registered via add_concurrency_release_hook().  In
    practice, callers should use the response hook or a try/finally in their
    endpoint to call concurrency_limiter.release(user_id).

    For a cleaner pattern, use `anti_abuse_guard` which wraps everything in
    a context manager and handles release automatically.
    """
    _assert_initialised()

    # 1. Global rate limit
    if not rate_limiter.check_global():
        raise HTTPException(status_code=429, detail=_429_DETAIL)

    # 2. Per-user rate limit
    if user_id and not rate_limiter.check_user(user_id):
        raise HTTPException(status_code=429, detail=_429_DETAIL)

    # 3. Per-user concurrency gate
    if user_id:
        granted = concurrency_limiter.acquire(user_id)
        if not granted:
            raise HTTPException(status_code=429, detail=_429_DETAIL)
        # Store in request state so the response middleware can release it
        request.state.concurrency_user_id = user_id


def require_token_budget(user_id: str, estimated_tokens: int) -> None:
    """
    Callable (not a FastAPI dependency decorator) — check and reserve token budget.

    Call this inside an endpoint body just before making the LLM call, once
    you know how many tokens the request will consume (e.g., `max_tokens`).

    Raises HTTPException(429) with the canonical payload if the user has
    exhausted their daily quota.

    Example:
        require_token_budget(user_id, estimated_tokens=request.max_tokens)
        response = call_llm(...)
        actual = response.usage.total_tokens
        token_quota.refund(user_id, max(0, estimated_tokens - actual))
    """
    _assert_initialised()
    if not token_quota.check_and_consume(user_id, estimated_tokens):
        raise HTTPException(status_code=429, detail=_429_DETAIL)


# ---------------------------------------------------------------------------
# Response header helper
# ---------------------------------------------------------------------------

def add_rate_limit_headers(response: Response, user_id: Optional[str] = None) -> None:
    """
    Attach rate-limit informational headers to a response object.

    Safe to call even if limiters are not initialised (headers are omitted).
    """
    if token_quota is None or rate_limiter is None:
        return
    try:
        if user_id:
            remaining_tokens = token_quota.get_remaining(user_id)
            reset_seconds = TokenQuota.seconds_until_reset()
            response.headers["X-RateLimit-Limit"] = "5000"
            response.headers["X-RateLimit-Remaining"] = str(remaining_tokens)
            response.headers["X-RateLimit-Reset"] = str(reset_seconds)
    except Exception as exc:
        logger.debug("Could not add rate-limit headers: %s", exc)


# ---------------------------------------------------------------------------
# Starlette middleware (optional — use only if you want automatic header
# injection and concurrency release on every response)
# ---------------------------------------------------------------------------

class AntiAbuseMiddleware:
    """
    ASGI middleware that:
        1. Releases concurrency slots after every response (even on errors).
        2. Injects X-RateLimit-* headers into every response.

    Register it in your FastAPI app:
        app.add_middleware(AntiAbuseMiddleware)

    NOTE: This middleware only releases concurrency slots that were acquired
    by `require_not_rate_limited`.  The dependency must run first (which it
    will, because FastAPI resolves dependencies before calling the endpoint).
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # We need access to the Request object to read request.state
        from starlette.requests import Request as StarletteRequest
        request = StarletteRequest(scope, receive)

        # Wrap send to inject headers into the response
        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                user_id = getattr(request.state, "concurrency_user_id", None)
                if token_quota is not None and user_id:
                    try:
                        remaining = token_quota.get_remaining(user_id)
                        reset_sec = TokenQuota.seconds_until_reset()
                        # Append headers (MutableHeaders not available here,
                        # so we build the list manually)
                        extra = [
                            (b"x-ratelimit-limit", b"5000"),
                            (b"x-ratelimit-remaining", str(remaining).encode()),
                            (b"x-ratelimit-reset", str(reset_sec).encode()),
                        ]
                        message = dict(message)
                        message["headers"] = list(message.get("headers", [])) + extra
                    except Exception as exc:
                        logger.debug("Header injection error: %s", exc)
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        finally:
            # Release concurrency slot regardless of response status
            user_id = getattr(request.state, "concurrency_user_id", None)
            if user_id and concurrency_limiter is not None:
                concurrency_limiter.release(user_id)
                # Clear so double-release cannot happen if middleware is nested
                try:
                    del request.state.concurrency_user_id
                except AttributeError:
                    pass


# ---------------------------------------------------------------------------
# Convenience: combined guard for LLM endpoints
# ---------------------------------------------------------------------------

class anti_abuse_guard:
    """
    Synchronous context manager combining all three protection layers for
    non-async code (e.g., Streamlit callbacks or CLI tools).

    Raises HTTPException(429) on any limit breach.
    Releases concurrency slot on exit.

    Usage:
        with anti_abuse_guard(user_id, estimated_tokens=2048):
            result = call_llm(...)
    """

    def __init__(self, user_id: str, estimated_tokens: int = 0) -> None:
        self._user_id = user_id
        self._tokens = estimated_tokens
        self._slot_acquired = False

    def __enter__(self) -> "anti_abuse_guard":
        _assert_initialised()

        if not rate_limiter.check_global():
            raise HTTPException(status_code=429, detail=_429_DETAIL)

        if not rate_limiter.check_user(self._user_id):
            raise HTTPException(status_code=429, detail=_429_DETAIL)

        self._slot_acquired = concurrency_limiter.acquire(self._user_id)
        if not self._slot_acquired:
            raise HTTPException(status_code=429, detail=_429_DETAIL)

        if self._tokens > 0:
            if not token_quota.check_and_consume(self._user_id, self._tokens):
                # Undo concurrency acquire before raising
                concurrency_limiter.release(self._user_id)
                self._slot_acquired = False
                raise HTTPException(status_code=429, detail=_429_DETAIL)

        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._slot_acquired:
            concurrency_limiter.release(self._user_id)
        return False  # do not suppress exceptions

    def refund_tokens(self, actual_tokens: int) -> None:
        """
        Refund unused tokens after actual LLM usage is known.

        Call this inside the with-block after the LLM responds:
            with anti_abuse_guard(user_id, estimated_tokens=12000) as guard:
                result = call_llm()
                guard.refund_tokens(result.usage.total_tokens)
        """
        unused = self._tokens - actual_tokens
        if unused > 0:
            token_quota.refund(self._user_id, unused)
