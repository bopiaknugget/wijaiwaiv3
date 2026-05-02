"""
anti_abuse — production-grade API protection layer.

Modules:
    rate_limit   — global + per-user sliding-window rate limiting (Redis)
    token_limit  — daily LLM token quota with Bangkok-timezone reset
    concurrency  — per-user active-request concurrency gate
    middleware   — FastAPI dependency factories that wire the three layers together
"""
