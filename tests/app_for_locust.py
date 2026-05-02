"""
app_for_locust.py — Minimal FastAPI test application for load/integration testing.

Limits (intentionally tight to make failures observable quickly):
    Global  : 5 req/s,  20 req/min
    Per-user: 10 req/min
    Concurrency: max 2 simultaneous per user
    Daily token cap: 500 tokens

Every 429 response carries {"detail": "ถึง Rate Limit กรุณารอสักครู่"} to match
the production contract validated by the Locust tests.

Usage:
    python -m uvicorn tests.app_for_locust:app --port 8099 --log-level error
"""

import logging
import os

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from anti_abuse.middleware import (
    AntiAbuseMiddleware,
    add_rate_limit_headers,
    init_limiters,
    require_token_budget,
)
import anti_abuse.middleware as mw

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application setup
# ---------------------------------------------------------------------------

app = FastAPI(title="anti_abuse load-test harness", docs_url=None, redoc_url=None)

# Register the ASGI middleware BEFORE startup so it wraps every request.
# AntiAbuseMiddleware releases concurrency slots and injects X-RateLimit-* headers.
app.add_middleware(AntiAbuseMiddleware)

import redis as _redis_lib

REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_DB   = int(os.environ.get("REDIS_DB", "15"))

_429_DETAIL = "ถึง Rate Limit กรุณารอสักครู่"


@app.on_event("startup")
def startup() -> None:
    _rc = _redis_lib.Redis(
        host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB,
        socket_timeout=1.0, retry_on_timeout=False,
        max_connections=50, decode_responses=True,
    )
    init_limiters(
        redis_client=_rc,
        daily_token_limit=500,       # tight: exhausted after 5 × 100-token /llm calls
        max_concurrent=2,            # max 2 simultaneous per user
        global_1s_limit=5,           # 5 req/s across all users
        global_60s_limit=20,         # 20 req/min across all users
        user_60s_limit=10,           # 10 req/min per user
        user_3600s_limit=300,        # 300 req/hr per user (not the binding limit in tests)
    )
    logger.info("app_for_locust: anti_abuse limiters initialised against %s:%s db=%s", REDIS_HOST, REDIS_PORT, REDIS_DB)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_and_raise(user_id: str, request: Request) -> None:
    """Apply global + per-user rate limit + concurrency gate, raise 429 on denial."""
    # Global rate check
    if not mw.rate_limiter.check_global():
        raise HTTPException(status_code=429, detail=_429_DETAIL)
    # Per-user rate check
    if not mw.rate_limiter.check_user(user_id):
        raise HTTPException(status_code=429, detail=_429_DETAIL)
    # Concurrency gate
    granted = mw.concurrency_limiter.acquire(user_id)
    if not granted:
        raise HTTPException(status_code=429, detail=_429_DETAIL)
    # Store for AntiAbuseMiddleware to release on response
    request.state.concurrency_user_id = user_id


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/ping")
async def ping(
    request: Request,
    response: Response,
    user_id: str = Query(default="anonymous", description="Simulated user identifier"),
) -> dict:
    """
    Health / smoke endpoint.

    Rate-limited (global + per-user).  No token quota applied.
    Returns 200 {"status": "ok", "user_id": "<uid>"} when within limits.
    Returns 429 {"detail": "ถึง Rate Limit กรุณารอสักครู่"} when any limit exceeded.
    """
    _check_and_raise(user_id, request)
    add_rate_limit_headers(response, user_id)
    return {"status": "ok", "user_id": user_id}


@app.get("/llm")
async def llm(
    request: Request,
    response: Response,
    user_id: str = Query(default="anonymous", description="Simulated user identifier"),
    tokens: int = Query(default=100, ge=1, le=12_000, description="Tokens to consume"),
) -> dict:
    """
    Simulated LLM endpoint.

    Rate-limited (global + per-user + concurrency) AND token-quota checked.
    Each call reserves `tokens` tokens against the user's daily 500-token cap.

    Returns 200 {"status": "ok", "tokens_consumed": N} on success.
    Returns 429 {"detail": "ถึง Rate Limit กรุณารอสักครู่"} when any limit exceeded.
    """
    _check_and_raise(user_id, request)

    # Token quota check — this raises HTTPException(429) if budget exhausted
    require_token_budget(user_id, estimated_tokens=tokens)

    add_rate_limit_headers(response, user_id)
    return {"status": "ok", "user_id": user_id, "tokens_consumed": tokens}


# ---------------------------------------------------------------------------
# Global 429 handler — ensures every 429 from HTTPException uses the canonical
# Thai payload even if raised outside our helpers (e.g., FastAPI validation).
# ---------------------------------------------------------------------------

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 429:
        return JSONResponse(
            status_code=429,
            content={"detail": _429_DETAIL},
        )
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
