"""
rate_limit.py — Global and per-user sliding-window rate limiting.

Redis key schema
----------------
Global counters  (shared across all users / API keys):
    rl:v1:global:req:1s     — sorted set, 1-second window
    rl:v1:global:req:60s    — sorted set, 60-second window

Per-user counters (keyed by Google user-ID or API key):
    rl:v1:user:{uid}:req:60s    — sorted set, 60-second window
    rl:v1:user:{uid}:req:3600s  — sorted set, 1-hour window

Why sorted sets?
    Each request is stored as a member whose *score* is the Unix timestamp
    in milliseconds.  Entries older than the window are pruned atomically
    before the count check, giving a true sliding window with O(log N)
    operations.  The Lua script makes the prune-count-insert sequence
    atomic, preventing TOCTOU races under high concurrency.

Failure policy: FAIL-OPEN
    If Redis is unreachable (connection error, timeout) we log a warning and
    allow the request through.  This keeps the app alive during transient
    Redis hiccups.  Sustained Redis outages should trigger a separate alert.
"""

import logging
import time
import uuid
from dataclasses import dataclass

import redis

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lua script — atomic sliding-window check+insert
#
# KEYS[1]  : the sorted-set key
# ARGV[1]  : current timestamp in milliseconds (string)
# ARGV[2]  : window size in milliseconds (string)
# ARGV[3]  : limit (max allowed count within window)
# ARGV[4]  : TTL for the key in seconds (slightly larger than window)
#
# Returns  : 1 if the request is ALLOWED, 0 if DENIED
# ---------------------------------------------------------------------------
_SLIDING_WINDOW_LUA = """
local key        = KEYS[1]
local now_ms     = tonumber(ARGV[1])
local window_ms  = tonumber(ARGV[2])
local limit      = tonumber(ARGV[3])
local ttl_sec    = tonumber(ARGV[4])
local clear_before = now_ms - window_ms

-- Remove stale entries outside the current window
redis.call('ZREMRANGEBYSCORE', key, '-inf', clear_before)

-- Count remaining entries in the window
local count = redis.call('ZCARD', key)

if count < limit then
    -- Admit the request: generate a guaranteed-unique member ID using an
    -- atomic sequence counter on a companion key.  This avoids collisions
    -- that occur when multiple calls share the same now_ms timestamp and
    -- math.random() produces the same value under fakeredis/test conditions.
    local seq_key = key .. ':seq'
    local seq = redis.call('INCR', seq_key)
    redis.call('EXPIRE', seq_key, ttl_sec)
    local member = now_ms .. '-' .. seq
    redis.call('ZADD', key, now_ms, member)
    redis.call('EXPIRE', key, ttl_sec)
    return 1
end

return 0
"""


@dataclass(frozen=True)
class RateLimitConfig:
    """Immutable configuration for one rate-limit tier."""
    window_ms: int       # window size in milliseconds
    limit: int           # max requests allowed within window
    ttl_sec: int         # Redis key TTL (should be >= window_ms // 1000 + 1)


# ---------------------------------------------------------------------------
# Default tier configurations
# ---------------------------------------------------------------------------
GLOBAL_1S  = RateLimitConfig(window_ms=1_000,   limit=5,   ttl_sec=5)
GLOBAL_60S = RateLimitConfig(window_ms=60_000,  limit=200, ttl_sec=120)

# Per-user defaults — callers may override via RateLimiter constructor
USER_60S   = RateLimitConfig(window_ms=60_000,  limit=30,  ttl_sec=120)
USER_3600S = RateLimitConfig(window_ms=3_600_000, limit=300, ttl_sec=7200)


class RateLimiter:
    """
    Sliding-window rate limiter backed by Redis.

    Instantiate once at app startup and inject via FastAPI's dependency
    injection system.  Thread-safe — the underlying Redis client uses a
    connection pool.

    Args:
        redis_client : a connected redis.Redis instance (with decode_responses=True)
        global_tiers : sequence of RateLimitConfig applied to ALL requests
        user_tiers   : sequence of RateLimitConfig applied per user_id
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        global_tiers: tuple[RateLimitConfig, ...] = (GLOBAL_1S, GLOBAL_60S),
        user_tiers: tuple[RateLimitConfig, ...] = (USER_60S, USER_3600S),
    ) -> None:
        self._redis = redis_client
        self._global_tiers = global_tiers
        self._user_tiers = user_tiers
        # Register the Lua script once; Redis caches it by SHA
        self._script = redis_client.register_script(_SLIDING_WINDOW_LUA)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_global(self) -> bool:
        """
        Check global rate limits across all tiers.

        Returns True if the request is allowed, False if any global tier
        is exhausted.  On Redis error, returns True (fail-open).
        """
        now_ms = self._now_ms()
        for tier in self._global_tiers:
            key = f"rl:v1:global:req:{tier.window_ms}ms"
            allowed = self._run_script(key, now_ms, tier)
            if not allowed:
                logger.warning("Global rate limit hit — key=%s", key)
                return False
        return True

    def check_user(self, user_id: str) -> bool:
        """
        Check per-user rate limits across all tiers.

        Args:
            user_id: Google OAuth user ID (or API key string)

        Returns True if allowed, False if any user tier is exhausted.
        On Redis error, returns True (fail-open).
        """
        now_ms = self._now_ms()
        for tier in self._user_tiers:
            key = f"rl:v1:user:{user_id}:req:{tier.window_ms}ms"
            allowed = self._run_script(key, now_ms, tier)
            if not allowed:
                logger.warning(
                    "Per-user rate limit hit — user=%s key=%s", user_id, key
                )
                return False
        return True

    def check_all(self, user_id: str) -> bool:
        """
        Convenience: check global limits first, then per-user limits.

        Returns True only if ALL tiers pass.
        """
        return self.check_global() and self.check_user(user_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    def _run_script(self, key: str, now_ms: int, tier: RateLimitConfig) -> bool:
        """
        Execute the Lua script for a single (key, tier) pair.

        Returns True if allowed, False if denied.
        On any Redis exception, logs and returns True (fail-open).
        """
        try:
            result = self._script(
                keys=[key],
                args=[str(now_ms), str(tier.window_ms), str(tier.limit), str(tier.ttl_sec)],
            )
            return bool(result)
        except Exception as exc:
            # Catch all exceptions (redis.RedisError, TimeoutError, etc.) so that
            # any infrastructure failure results in fail-open, keeping the app alive.
            logger.error(
                "Redis error in rate limiter (fail-open) — key=%s error=%s",
                key, exc,
            )
            return True  # fail-open: keep app alive
