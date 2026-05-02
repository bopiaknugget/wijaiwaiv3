"""
token_limit.py — Daily LLM token quota with Bangkok-timezone reset.

Redis key schema
----------------
    tl:v1:user:{uid}:tokens:{YYYY-MM-DD}

    Where YYYY-MM-DD is the *Bangkok-local* date (Asia/Bangkok, UTC+7).
    The key is a plain Redis string holding the cumulative token count
    (input + output combined) consumed today by that user.

    TTL is set to 49 hours on every INCRBY so that:
    - The key survives exactly one full Bangkok day plus a 1-hour buffer
      to handle clock skew at the boundary.
    - Keys self-expire automatically without a cron job.

Quota reset
-----------
    The quota resets at midnight Bangkok time because the *date suffix* in the
    key rolls over at that moment.  There is no explicit cleanup needed —
    requests on 2026-04-03 naturally write to a different key than requests
    on 2026-04-02.

Atomic check-and-increment
--------------------------
    A Lua script atomically:
        1. GET the current count.
        2. Decide whether adding `requested_tokens` would exceed the daily cap.
        3. If within budget: INCRBY and EXPIRE, return new total.
        4. If over budget: return -1 (denied) without mutating state.

    This prevents two concurrent requests from both reading "4900 tokens used"
    and both deciding they can add 200, which would push the total to 5100.

Failure policy: FAIL-OPEN (same rationale as rate_limit.py)
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import redis

logger = logging.getLogger(__name__)

_BANGKOK_TZ = ZoneInfo("Asia/Bangkok")

DAILY_TOKEN_LIMIT: int = 5_000   # hard cap per user per Bangkok day
_KEY_TTL_SEC: int = 49 * 3600    # 49-hour TTL; survives the day + buffer

# ---------------------------------------------------------------------------
# Lua script — atomic check-then-increment
#
# KEYS[1]  : tl:v1:user:{uid}:tokens:{YYYY-MM-DD}
# ARGV[1]  : tokens being requested now (int as string)
# ARGV[2]  : daily limit (int as string)
# ARGV[3]  : TTL in seconds (int as string)
#
# Returns  : new total (integer >= 1) if ALLOWED
#            -1                        if DENIED (would exceed limit)
# ---------------------------------------------------------------------------
_TOKEN_CHECK_LUA = """
local key       = KEYS[1]
local requested = tonumber(ARGV[1])
local cap       = tonumber(ARGV[2])
local ttl       = tonumber(ARGV[3])

local current = tonumber(redis.call('GET', key) or '0')

if current + requested > cap then
    return -1
end

local new_total = redis.call('INCRBY', key, requested)
redis.call('EXPIRE', key, ttl)
return new_total
"""


def _bangkok_date_str() -> str:
    """Return today's date in Bangkok timezone as 'YYYY-MM-DD'."""
    return datetime.now(_BANGKOK_TZ).strftime("%Y-%m-%d")


def _token_key(user_id: str, date_str: str) -> str:
    return f"tl:v1:user:{user_id}:tokens:{date_str}"


class TokenQuota:
    """
    Daily LLM token quota enforcer.

    Instantiate once at app startup; inject via FastAPI dependency injection.

    Args:
        redis_client  : connected redis.Redis instance (decode_responses=True)
        daily_limit   : max combined tokens (input+output) per user per day
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        daily_limit: int = DAILY_TOKEN_LIMIT,
    ) -> None:
        self._redis = redis_client
        self._limit = daily_limit
        self._script = redis_client.register_script(_TOKEN_CHECK_LUA)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_and_consume(self, user_id: str, tokens: int) -> bool:
        """
        Atomically verify that consuming `tokens` does not exceed today's cap,
        and if so, increment the counter.

        Args:
            user_id : Google OAuth user ID
            tokens  : number of tokens about to be consumed (pre-flight estimate)
                      Callers should use a conservative estimate (e.g., max_tokens
                      for the request) to reserve budget before calling the LLM.

        Returns:
            True  — within budget; counter has been incremented.
            False — would exceed today's cap; counter unchanged.

        On Redis error: returns True (fail-open).
        """
        date_str = _bangkok_date_str()
        key = _token_key(user_id, date_str)
        try:
            result = self._script(
                keys=[key],
                args=[str(tokens), str(self._limit), str(_KEY_TTL_SEC)],
            )
            if result == -1:
                logger.warning(
                    "Daily token quota exceeded — user=%s date=%s tokens_requested=%d",
                    user_id, date_str, tokens,
                )
                return False
            logger.debug(
                "Token budget update — user=%s date=%s new_total=%d limit=%d",
                user_id, date_str, result, self._limit,
            )
            return True
        except Exception as exc:
            logger.error(
                "Redis error in token quota (fail-open) — user=%s error=%s",
                user_id, exc,
            )
            return True  # fail-open

    def get_remaining(self, user_id: str) -> int:
        """
        Return the number of tokens the user can still consume today.
        Useful for populating X-RateLimit-Remaining headers or UI indicators.

        On Redis error: returns the full daily limit (optimistic).
        """
        date_str = _bangkok_date_str()
        key = _token_key(user_id, date_str)
        try:
            raw = self._redis.get(key)
            used = int(raw) if raw is not None else 0
            return max(0, self._limit - used)
        except Exception as exc:
            logger.error(
                "Redis error in get_remaining (returning full limit) — user=%s error=%s",
                user_id, exc,
            )
            return self._limit

    def get_used(self, user_id: str) -> int:
        """Return the number of tokens consumed today (0 if none or Redis error)."""
        date_str = _bangkok_date_str()
        key = _token_key(user_id, date_str)
        try:
            raw = self._redis.get(key)
            return int(raw) if raw is not None else 0
        except Exception as exc:
            logger.error(
                "Redis error in get_used — user=%s error=%s", user_id, exc,
            )
            return 0

    def refund(self, user_id: str, tokens: int) -> None:
        """
        Refund tokens back to the user's daily budget.

        Call this when a pre-flight reservation was made but the LLM call
        ultimately consumed fewer tokens (or failed before consuming any).
        The counter is decremented by `tokens`, clamped to 0.

        Uses DECRBY which is atomic.  A separate EXPIRE is issued to refresh
        the TTL (Redis DECRBY does not renew TTL).

        On Redis error: silently ignored (refund is best-effort).
        """
        if tokens <= 0:
            return
        date_str = _bangkok_date_str()
        key = _token_key(user_id, date_str)
        try:
            pipe = self._redis.pipeline(transaction=True)
            pipe.decrby(key, tokens)
            # Clamp at 0 via a second read is non-atomic; instead we accept that
            # a refund can push the counter slightly negative in edge cases.
            # EXPIRE to refresh key lifetime.
            pipe.expire(key, _KEY_TTL_SEC)
            results = pipe.execute()
            new_val = results[0]
            if new_val < 0:
                # Clamp to 0 — set only if key still exists (race-safe enough)
                self._redis.set(key, 0, keepttl=True)
            logger.debug(
                "Token refund — user=%s date=%s refunded=%d", user_id, date_str, tokens
            )
        except Exception as exc:
            logger.error(
                "Redis error during token refund (ignored) — user=%s error=%s",
                user_id, exc,
            )

    # ------------------------------------------------------------------
    # Informational helpers
    # ------------------------------------------------------------------

    @staticmethod
    def seconds_until_reset() -> int:
        """
        Return the number of seconds until midnight Bangkok time.
        Useful for Retry-After headers when the token quota is exhausted.
        """
        now = datetime.now(_BANGKOK_TZ)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        # Roll forward to next midnight
        from datetime import timedelta
        next_midnight = midnight + timedelta(days=1)
        delta = next_midnight - now
        return int(delta.total_seconds())
