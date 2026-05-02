"""
concurrency.py — Per-user active-request concurrency gate.

Redis key schema
----------------
    cc:v1:user:{uid}:active

    A plain Redis string (integer counter) tracking the number of requests
    currently in-flight for a given user.  The counter is incremented at
    request entry and decremented at request exit (via a context manager).

    TTL: 300 seconds (5 minutes).  This acts as a safety net in case a
    server process crashes mid-request and never decrements.  The TTL is
    refreshed on every acquire, so a long-running but healthy request will
    not be evicted.

Why not a sorted set?
    Concurrency is about *simultaneous* requests, not requests over time.
    A simple INCR / DECR on a string key is sufficient and far cheaper
    than a sorted set.

Atomic check-and-increment
---------------------------
    A Lua script atomically:
        1. GET the current active count.
        2. If count < max_concurrent: INCR and EXPIRE, return new count.
        3. Else: return -1 (denied).

    This prevents two simultaneous requests from both reading "2 active"
    (below a limit of 3) and both incrementing to create 4.

Failure policy: FAIL-OPEN
    If Redis is unreachable the gate is bypassed and the request is allowed.

Usage pattern (in a FastAPI dependency):
    async with concurrency_limiter.acquire(user_id):
        # process request
        ...

    The context manager guarantees DECR even if an exception is raised.
"""

import logging
from contextlib import asynccontextmanager, contextmanager

import redis

logger = logging.getLogger(__name__)

MAX_CONCURRENT_PER_USER: int = 3   # hard ceiling on simultaneous in-flight requests
_KEY_TTL_SEC: int = 300            # 5-minute safety-net TTL


# ---------------------------------------------------------------------------
# Lua script — atomic check-then-increment
#
# KEYS[1]  : cc:v1:user:{uid}:active
# ARGV[1]  : max concurrent limit (int as string)
# ARGV[2]  : TTL in seconds (int as string)
#
# Returns  : new count (>= 1) if ALLOWED
#            -1               if DENIED (at or above limit)
# ---------------------------------------------------------------------------
_ACQUIRE_LUA = """
local key     = KEYS[1]
local maxc    = tonumber(ARGV[1])
local ttl     = tonumber(ARGV[2])

local current = tonumber(redis.call('GET', key) or '0')

if current >= maxc then
    return -1
end

local new_val = redis.call('INCR', key)
redis.call('EXPIRE', key, ttl)
return new_val
"""


def _concurrency_key(user_id: str) -> str:
    return f"cc:v1:user:{user_id}:active"


class ConcurrencyLimiter:
    """
    Per-user concurrency gate backed by Redis.

    Instantiate once at app startup; inject via FastAPI dependency injection.

    Args:
        redis_client    : connected redis.Redis instance (decode_responses=True)
        max_concurrent  : max simultaneous in-flight requests per user
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        max_concurrent: int = MAX_CONCURRENT_PER_USER,
    ) -> None:
        self._redis = redis_client
        self._max = max_concurrent
        self._script = redis_client.register_script(_ACQUIRE_LUA)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def acquire(self, user_id: str) -> bool:
        """
        Attempt to acquire a concurrency slot for user_id.

        Returns True  — slot granted; caller MUST call release() when done.
        Returns False — at concurrency limit; request should be rejected.

        On Redis error: returns True (fail-open) without requiring release().
        """
        key = _concurrency_key(user_id)
        try:
            result = self._script(
                keys=[key],
                args=[str(self._max), str(_KEY_TTL_SEC)],
            )
            if result == -1:
                logger.warning(
                    "Concurrency limit hit — user=%s active_limit=%d",
                    user_id, self._max,
                )
                return False
            logger.debug(
                "Concurrency slot acquired — user=%s active=%d", user_id, result
            )
            return True
        except Exception as exc:
            logger.error(
                "Redis error in concurrency acquire (fail-open) — user=%s error=%s",
                user_id, exc,
            )
            return True  # fail-open: do NOT require release for this path

    def release(self, user_id: str) -> None:
        """
        Release one concurrency slot for user_id.

        Decrements the counter, clamped at 0.  Safe to call even if the
        key has already expired (DECR on a missing key would set it to -1,
        which we prevent with a floor clamp in a pipeline).

        On Redis error: silently ignored — the safety-net TTL will reclaim
        the slot eventually.
        """
        key = _concurrency_key(user_id)
        try:
            pipe = self._redis.pipeline(transaction=True)
            pipe.decr(key)
            pipe.expire(key, _KEY_TTL_SEC)
            results = pipe.execute()
            new_val = results[0]
            if new_val < 0:
                # Counter went negative — clamp to 0
                self._redis.set(key, 0, keepttl=True)
            logger.debug(
                "Concurrency slot released — user=%s active=%d", user_id, max(0, new_val)
            )
        except Exception as exc:
            logger.error(
                "Redis error in concurrency release (ignored) — user=%s error=%s",
                user_id, exc,
            )

    def get_active(self, user_id: str) -> int:
        """Return the current active request count for user_id (0 on error)."""
        key = _concurrency_key(user_id)
        try:
            raw = self._redis.get(key)
            return max(0, int(raw)) if raw is not None else 0
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # Context manager helpers
    # ------------------------------------------------------------------

    @contextmanager
    def slot(self, user_id: str):
        """
        Synchronous context manager for non-async request handlers.

        Usage:
            granted = limiter.acquire(user_id)
            if not granted:
                raise HTTPException(429, ...)
            with limiter.slot(user_id):
                ...

        NOTE: This context manager calls release() but does NOT re-check the
        acquire; callers must check acquire() themselves before entering.
        Only use this to wrap the work unit so release is guaranteed.

        Simpler pattern — use `managed_acquire` instead:
            with limiter.managed_acquire(user_id) as granted:
                if not granted:
                    raise HTTPException(429, ...)
        """
        try:
            yield
        finally:
            self.release(user_id)

    @contextmanager
    def managed_acquire(self, user_id: str):
        """
        Synchronous context manager that handles acquire + release atomically.

        Yields True if the slot was granted, False if denied.
        On True: release() is called on exit (even on exception).
        On False: no release is needed (slot was never acquired).

        Usage:
            with limiter.managed_acquire(user_id) as granted:
                if not granted:
                    raise HTTPException(status_code=429, detail="ถึง Rate Limit กรุณารอสักครู่")
                # do work
        """
        granted = self.acquire(user_id)
        try:
            yield granted
        finally:
            if granted:
                self.release(user_id)

    @asynccontextmanager
    async def async_managed_acquire(self, user_id: str):
        """
        Async context manager — same semantics as managed_acquire but for
        async FastAPI endpoints.

        Usage:
            async with limiter.async_managed_acquire(user_id) as granted:
                if not granted:
                    raise HTTPException(status_code=429, detail="ถึง Rate Limit กรุณารอสักครู่")
                result = await some_async_work()
        """
        granted = self.acquire(user_id)
        try:
            yield granted
        finally:
            if granted:
                self.release(user_id)
