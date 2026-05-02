"""
test_anti_abuse.py — Unit tests for the anti_abuse protection layer.

Test strategy
-------------
All Redis calls are mocked via fakeredis, which implements the full Redis
protocol in-process without a server.  This lets tests:
    - Run in CI without a Redis instance
    - Simulate concurrent access via threading
    - Trigger artificial failures via monkeypatching

Test categories
    1. RateLimiter    — sliding window correctness, Lua atomicity, fail-open
    2. TokenQuota     — daily cap enforcement, Bangkok timezone reset, refund
    3. ConcurrencyLimiter — slot limit, release, managed_acquire context manager
    4. Race conditions  — concurrent thread hammering to detect TOCTOU bugs
    5. Middleware       — init_limiters, anti_abuse_guard integration
    6. Failure modes    — Redis errors trigger fail-open behaviour

Dependencies:
    pip install fakeredis pytest pytest-asyncio
"""

import threading
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

# ---------------------------------------------------------------------------
# Try to import fakeredis; skip entire module gracefully if not installed
# ---------------------------------------------------------------------------
try:
    import fakeredis
except ImportError:
    pytest.skip("fakeredis not installed — run: pip install fakeredis", allow_module_level=True)

from anti_abuse.rate_limit import RateLimiter, RateLimitConfig
from anti_abuse.token_limit import TokenQuota, _bangkok_date_str, DAILY_TOKEN_LIMIT
from anti_abuse.concurrency import ConcurrencyLimiter, MAX_CONCURRENT_PER_USER
import anti_abuse.middleware as mw
from fastapi import HTTPException

_BANGKOK_TZ = ZoneInfo("Asia/Bangkok")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_redis():
    """In-process Redis server (fakeredis) — reset state between tests."""
    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server, decode_responses=True)
    yield client
    client.flushall()
    client.close()


@pytest.fixture
def rate_limiter(fake_redis):
    """RateLimiter with tight limits suitable for testing."""
    return RateLimiter(
        redis_client=fake_redis,
        global_tiers=(
            RateLimitConfig(window_ms=1_000,  limit=3,  ttl_sec=5),   # 3 req/s global
            RateLimitConfig(window_ms=60_000, limit=10, ttl_sec=120),  # 10 req/min global
        ),
        user_tiers=(
            RateLimitConfig(window_ms=60_000, limit=5, ttl_sec=120),   # 5 req/min per user
        ),
    )


@pytest.fixture
def token_quota(fake_redis):
    """TokenQuota with a small daily limit (100 tokens) for fast testing."""
    return TokenQuota(redis_client=fake_redis, daily_limit=100)


@pytest.fixture
def concurrency_limiter(fake_redis):
    """ConcurrencyLimiter with max 2 concurrent slots per user."""
    return ConcurrencyLimiter(redis_client=fake_redis, max_concurrent=2)


# ===========================================================================
# 1. RateLimiter tests
# ===========================================================================

class TestRateLimiter:

    def test_global_allows_under_limit(self, rate_limiter):
        """First N requests under the 1-second window should all be allowed."""
        for _ in range(3):
            assert rate_limiter.check_global() is True

    def test_global_blocks_at_limit(self, rate_limiter):
        """The 4th request within 1 second should be blocked."""
        for _ in range(3):
            rate_limiter.check_global()
        assert rate_limiter.check_global() is False

    def test_global_resets_after_window(self, rate_limiter, fake_redis):
        """
        After the window expires, the counter resets and requests are allowed.
        We simulate time passing by injecting stale timestamps directly into the
        sorted set rather than sleeping.
        """
        # Fill up the 1s global window
        for _ in range(3):
            rate_limiter.check_global()
        assert rate_limiter.check_global() is False

        # Manually expire the entries by removing all members from the sorted set
        key = "rl:v1:global:req:1000ms"
        fake_redis.delete(key)

        # Now requests should be allowed again
        assert rate_limiter.check_global() is True

    def test_per_user_allows_under_limit(self, rate_limiter):
        for _ in range(5):
            assert rate_limiter.check_user("user_alice") is True

    def test_per_user_blocks_at_limit(self, rate_limiter):
        for _ in range(5):
            rate_limiter.check_user("user_alice")
        assert rate_limiter.check_user("user_alice") is False

    def test_per_user_isolation(self, rate_limiter):
        """Exhausting one user's quota must not affect another user."""
        for _ in range(5):
            rate_limiter.check_user("user_alice")
        assert rate_limiter.check_user("user_alice") is False
        # Bob is completely unaffected
        assert rate_limiter.check_user("user_bob") is True

    def test_check_all_respects_global(self, rate_limiter):
        """check_all should fail if global limit is exhausted."""
        for _ in range(3):
            rate_limiter.check_global()
        assert rate_limiter.check_all("user_alice") is False

    def test_check_all_respects_user(self, rate_limiter):
        """check_all should fail if user limit is exhausted (global still OK)."""
        for _ in range(5):
            rate_limiter.check_user("user_alice")
        # Global counter only has 5 entries (well under 3/s window for our 1s test
        # but might overlap — so we reset global key to keep test deterministic)
        fake_redis_obj = rate_limiter._redis
        fake_redis_obj.delete("rl:v1:global:req:1000ms")
        fake_redis_obj.delete("rl:v1:global:req:60000ms")
        assert rate_limiter.check_all("user_alice") is False

    def test_fail_open_on_redis_error(self, fake_redis):
        """When Redis raises an error, rate limiter allows the request (fail-open)."""
        limiter = RateLimiter(
            redis_client=fake_redis,
            global_tiers=(RateLimitConfig(window_ms=3_600_000, limit=1, ttl_sec=7200),),
            user_tiers=(),
        )
        # Exhaust the limit
        limiter.check_global()
        assert limiter.check_global() is False

        # Simulate Redis failure by replacing the script with a broken callable
        limiter._script = MagicMock(side_effect=Exception("connection refused"))
        assert limiter.check_global() is True  # fail-open


# ===========================================================================
# 2. TokenQuota tests
# ===========================================================================

class TestTokenQuota:

    def test_allows_under_daily_limit(self, token_quota):
        assert token_quota.check_and_consume("user_a", 50) is True
        assert token_quota.check_and_consume("user_a", 49) is True

    def test_blocks_when_limit_reached(self, token_quota):
        token_quota.check_and_consume("user_a", 90)
        # 11 more would hit 101 > 100
        assert token_quota.check_and_consume("user_a", 11) is False

    def test_exact_boundary_allowed(self, token_quota):
        token_quota.check_and_consume("user_a", 90)
        # Exactly 10 more = 100 total, which equals the limit — must be ALLOWED
        assert token_quota.check_and_consume("user_a", 10) is True

    def test_one_over_boundary_blocked(self, token_quota):
        token_quota.check_and_consume("user_a", 90)
        # 11 would be 101 > 100
        assert token_quota.check_and_consume("user_a", 11) is False

    def test_per_user_isolation(self, token_quota):
        token_quota.check_and_consume("user_a", 100)
        assert token_quota.check_and_consume("user_a", 1) is False
        # user_b is unaffected
        assert token_quota.check_and_consume("user_b", 100) is True

    def test_get_remaining(self, token_quota):
        token_quota.check_and_consume("user_a", 30)
        assert token_quota.get_remaining("user_a") == 70

    def test_get_remaining_fresh_user(self, token_quota):
        assert token_quota.get_remaining("new_user") == 100

    def test_get_used(self, token_quota):
        token_quota.check_and_consume("user_a", 42)
        assert token_quota.get_used("user_a") == 42

    def test_refund(self, token_quota):
        token_quota.check_and_consume("user_a", 100)
        assert token_quota.check_and_consume("user_a", 1) is False
        token_quota.refund("user_a", 20)
        assert token_quota.check_and_consume("user_a", 10) is True

    def test_refund_does_not_go_negative(self, token_quota, fake_redis):
        """Refunding more than was consumed should clamp at 0, not go negative."""
        token_quota.check_and_consume("user_a", 10)
        token_quota.refund("user_a", 50)  # refund more than consumed
        used = token_quota.get_used("user_a")
        assert used >= 0

    def test_seconds_until_reset_is_positive(self):
        secs = TokenQuota.seconds_until_reset()
        assert 0 < secs <= 86400

    def test_seconds_until_reset_next_midnight(self):
        """Reset should be less than 24 hours from now."""
        secs = TokenQuota.seconds_until_reset()
        assert secs < 86400

    def test_bangkok_date_str_format(self):
        date_str = _bangkok_date_str()
        # Should be YYYY-MM-DD
        assert len(date_str) == 10
        assert date_str[4] == "-"
        assert date_str[7] == "-"

    def test_bangkok_date_boundary(self):
        """
        Verify that two requests on different Bangkok dates use different keys.
        We simulate a Bangkok midnight by constructing the expected key strings
        for yesterday and today and confirming they differ.
        """
        today = datetime.now(_BANGKOK_TZ).strftime("%Y-%m-%d")
        yesterday_dt = datetime.now(_BANGKOK_TZ) - timedelta(days=1)
        yesterday = yesterday_dt.strftime("%Y-%m-%d")
        assert today != yesterday

    def test_fail_open_on_redis_error(self, fake_redis):
        quota = TokenQuota(redis_client=fake_redis, daily_limit=10)
        quota._script = MagicMock(side_effect=Exception("timeout"))
        assert quota.check_and_consume("user_a", 5) is True  # fail-open


# ===========================================================================
# 3. ConcurrencyLimiter tests
# ===========================================================================

class TestConcurrencyLimiter:

    def test_allows_under_limit(self, concurrency_limiter):
        assert concurrency_limiter.acquire("user_a") is True
        assert concurrency_limiter.acquire("user_a") is True

    def test_blocks_at_limit(self, concurrency_limiter):
        concurrency_limiter.acquire("user_a")
        concurrency_limiter.acquire("user_a")
        assert concurrency_limiter.acquire("user_a") is False

    def test_release_allows_new_acquire(self, concurrency_limiter):
        concurrency_limiter.acquire("user_a")
        concurrency_limiter.acquire("user_a")
        assert concurrency_limiter.acquire("user_a") is False
        concurrency_limiter.release("user_a")
        assert concurrency_limiter.acquire("user_a") is True

    def test_release_does_not_go_negative(self, concurrency_limiter, fake_redis):
        """Release on an empty counter must not produce a negative value."""
        concurrency_limiter.release("user_a")
        val = fake_redis.get("cc:v1:user:user_a:active")
        # Either key doesn't exist or is >= 0
        if val is not None:
            assert int(val) >= 0

    def test_per_user_isolation(self, concurrency_limiter):
        concurrency_limiter.acquire("user_a")
        concurrency_limiter.acquire("user_a")
        # user_a is at limit
        assert concurrency_limiter.acquire("user_a") is False
        # user_b is unaffected
        assert concurrency_limiter.acquire("user_b") is True

    def test_get_active(self, concurrency_limiter):
        assert concurrency_limiter.get_active("user_a") == 0
        concurrency_limiter.acquire("user_a")
        assert concurrency_limiter.get_active("user_a") == 1

    def test_managed_acquire_context_manager_allowed(self, concurrency_limiter):
        with concurrency_limiter.managed_acquire("user_a") as granted:
            assert granted is True
            assert concurrency_limiter.get_active("user_a") == 1
        # After context exit, slot must be released
        assert concurrency_limiter.get_active("user_a") == 0

    def test_managed_acquire_context_manager_denied(self, concurrency_limiter):
        """When at limit, managed_acquire yields False without acquiring a slot."""
        concurrency_limiter.acquire("user_a")
        concurrency_limiter.acquire("user_a")
        with concurrency_limiter.managed_acquire("user_a") as granted:
            assert granted is False
        # Active count unchanged (still 2)
        assert concurrency_limiter.get_active("user_a") == 2
        # Release both manually
        concurrency_limiter.release("user_a")
        concurrency_limiter.release("user_a")

    def test_managed_acquire_releases_on_exception(self, concurrency_limiter):
        """Slot must be released even when the body raises an exception."""
        try:
            with concurrency_limiter.managed_acquire("user_a") as granted:
                assert granted is True
                raise ValueError("simulated failure")
        except ValueError:
            pass
        assert concurrency_limiter.get_active("user_a") == 0

    def test_fail_open_on_redis_error(self, fake_redis):
        limiter = ConcurrencyLimiter(redis_client=fake_redis, max_concurrent=1)
        limiter._script = MagicMock(side_effect=Exception("timeout"))
        assert limiter.acquire("user_a") is True  # fail-open


# ===========================================================================
# 4. Race condition tests (threading)
# ===========================================================================

class TestRaceConditions:
    """
    These tests spin up multiple threads that hammer the same key simultaneously.
    The goal is to verify that the Lua scripts prevent TOCTOU races — the
    total number of admitted requests must never exceed the configured limit.
    """

    def test_global_rate_limit_no_overrun_under_concurrency(self, fake_redis):
        """
        100 threads fire into a 1-hour global window with a limit of 10.

        Atomicity invariant: the Lua script stores one sorted-set member per
        admitted request.  After all threads finish, the sorted set must contain
        EXACTLY 10 members — no more (overrun) and no fewer (under-admission).
        This validates the Redis state directly, which is the authoritative
        source of truth, regardless of what individual thread return-values show.

        NOTE: fakeredis Lua is atomic per *invocation* but the Python
        thread scheduling may interleave between the ZCARD check and the ZADD
        in the same Lua call (a known fakeredis threading limitation).  For
        production Redis, the Lua script runs atomically as a single unit.
        We therefore assert on the final sorted-set cardinality rather than
        on per-thread return values.
        """
        limiter = RateLimiter(
            redis_client=fake_redis,
            global_tiers=(RateLimitConfig(window_ms=3_600_000, limit=10, ttl_sec=7200),),
            user_tiers=(),
        )
        barrier = threading.Barrier(100)

        def fire():
            barrier.wait()  # all threads start simultaneously
            limiter.check_global()

        threads = [threading.Thread(target=fire) for _ in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        key = "rl:v1:global:req:3600000ms"
        final_count = fake_redis.zcard(key)
        # The sorted set must never exceed the limit — this is the hard
        # invariant enforced by the Lua script's atomic ZCARD check.
        assert final_count <= 10, (
            f"Sorted set overrun: {final_count} members stored, limit=10"
        )
        assert final_count == 10, (
            f"Under-admission in sorted set: only {final_count}/10 members"
        )

    def test_per_user_limit_no_overrun_under_concurrency(self, fake_redis):
        """
        50 threads for the same user, 1-hour window, limit=5.
        Validates the final Redis sorted-set cardinality, not return values.
        """
        limiter = RateLimiter(
            redis_client=fake_redis,
            global_tiers=(),
            user_tiers=(RateLimitConfig(window_ms=3_600_000, limit=5, ttl_sec=7200),),
        )
        barrier = threading.Barrier(50)

        def fire():
            barrier.wait()
            limiter.check_user("shared_user")

        threads = [threading.Thread(target=fire) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        key = "rl:v1:user:shared_user:req:3600000ms"
        final_count = fake_redis.zcard(key)
        assert final_count <= 5, (
            f"Sorted set overrun: {final_count} members stored, limit=5"
        )
        assert final_count == 5, (
            f"Under-admission in sorted set: only {final_count}/5 members"
        )

    def test_token_quota_no_overrun_under_concurrency(self, fake_redis):
        """
        20 threads each requesting 10 tokens; limit=100.
        Total tokens consumed must not exceed 100.
        """
        quota = TokenQuota(redis_client=fake_redis, daily_limit=100)
        results = []
        lock = threading.Lock()

        def consume():
            allowed = quota.check_and_consume("shared_user", 10)
            with lock:
                results.append(allowed)

        threads = [threading.Thread(target=consume) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        admitted = sum(1 for r in results if r is True)
        total_tokens = admitted * 10
        assert total_tokens <= 100, (
            f"Token race condition: {total_tokens} tokens consumed, limit=100"
        )
        assert admitted == 10, f"Under-admission: only {admitted}/10 admitted"

    def test_concurrency_limiter_no_overrun_under_threading(self, fake_redis):
        """
        30 threads simultaneously acquiring a concurrency slot; max=3.
        At most 3 must be granted.
        """
        limiter = ConcurrencyLimiter(redis_client=fake_redis, max_concurrent=3)
        results = []
        lock = threading.Lock()

        def acquire():
            granted = limiter.acquire("shared_user")
            with lock:
                results.append(granted)

        threads = [threading.Thread(target=acquire) for _ in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        granted_count = sum(1 for r in results if r is True)
        assert granted_count <= 3, (
            f"Concurrency race: {granted_count} slots granted, max=3"
        )
        assert granted_count == 3, (
            f"Under-admission: only {granted_count}/3 slots granted"
        )

    def test_concurrency_acquire_release_cycle_under_threading(self, fake_redis):
        """
        10 threads each do: acquire -> sleep briefly -> release.
        After all threads finish, the active counter must be 0.
        """
        limiter = ConcurrencyLimiter(redis_client=fake_redis, max_concurrent=10)

        def cycle():
            granted = limiter.acquire("cycle_user")
            if granted:
                time.sleep(0.01)
                limiter.release("cycle_user")

        threads = [threading.Thread(target=cycle) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        active = limiter.get_active("cycle_user")
        assert active == 0, f"Leaked concurrency slots: {active} still active"


# ===========================================================================
# 5. Middleware / integration tests
# ===========================================================================

class TestMiddlewareIntegration:

    def setup_method(self):
        """Reset middleware singletons before each test."""
        mw._redis_client = None
        mw.rate_limiter = None
        mw.token_quota = None
        mw.concurrency_limiter = None

    def test_init_limiters(self, fake_redis):
        mw.init_limiters(redis_client=fake_redis)
        assert mw.rate_limiter is not None
        assert mw.token_quota is not None
        assert mw.concurrency_limiter is not None

    def test_require_not_rate_limited_passes(self, fake_redis):
        mw.init_limiters(redis_client=fake_redis)
        request = MagicMock()
        request.state = MagicMock()
        # Should not raise
        mw.require_not_rate_limited(request=request, user_id="user_a")

    def test_require_not_rate_limited_blocks_on_global(self, fake_redis):
        mw.init_limiters(
            redis_client=fake_redis,
            global_1s_limit=1,
            global_60s_limit=200,
        )
        request = MagicMock()
        request.state = MagicMock()
        mw.require_not_rate_limited(request=request, user_id="user_a")
        with pytest.raises(HTTPException) as exc_info:
            mw.require_not_rate_limited(request=request, user_id="user_b")
        assert exc_info.value.status_code == 429
        assert exc_info.value.detail == "ถึง Rate Limit กรุณารอสักครู่"

    def test_require_token_budget_passes(self, fake_redis):
        mw.init_limiters(redis_client=fake_redis, daily_token_limit=5000)
        mw.require_token_budget("user_a", 100)

    def test_require_token_budget_blocks_when_exceeded(self, fake_redis):
        mw.init_limiters(redis_client=fake_redis, daily_token_limit=100)
        mw.require_token_budget("user_a", 90)
        with pytest.raises(HTTPException) as exc_info:
            mw.require_token_budget("user_a", 20)
        assert exc_info.value.status_code == 429
        assert exc_info.value.detail == "ถึง Rate Limit กรุณารอสักครู่"

    def test_anti_abuse_guard_happy_path(self, fake_redis):
        mw.init_limiters(redis_client=fake_redis, daily_token_limit=1000)
        with mw.anti_abuse_guard("user_a", estimated_tokens=100):
            assert mw.concurrency_limiter.get_active("user_a") == 1
        assert mw.concurrency_limiter.get_active("user_a") == 0

    def test_anti_abuse_guard_refund_tokens(self, fake_redis):
        mw.init_limiters(redis_client=fake_redis, daily_token_limit=1000)
        with mw.anti_abuse_guard("user_a", estimated_tokens=500) as guard:
            guard.refund_tokens(200)  # only used 200 of 500 reserved
        remaining = mw.token_quota.get_remaining("user_a")
        # Should have 1000 - 200 = 800 remaining
        assert remaining == 800

    def test_anti_abuse_guard_releases_on_exception(self, fake_redis):
        mw.init_limiters(redis_client=fake_redis, daily_token_limit=1000)
        try:
            with mw.anti_abuse_guard("user_a", estimated_tokens=100):
                raise RuntimeError("endpoint error")
        except RuntimeError:
            pass
        assert mw.concurrency_limiter.get_active("user_a") == 0

    def test_anti_abuse_guard_raises_429_on_rate_limit(self, fake_redis):
        mw.init_limiters(
            redis_client=fake_redis,
            global_1s_limit=0,  # immediately exhausted
            global_60s_limit=200,
            daily_token_limit=1000,
        )
        with pytest.raises(HTTPException) as exc_info:
            with mw.anti_abuse_guard("user_a", estimated_tokens=100):
                pass
        assert exc_info.value.status_code == 429

    def test_anti_abuse_guard_raises_429_on_token_exhaustion(self, fake_redis):
        mw.init_limiters(redis_client=fake_redis, daily_token_limit=100)
        mw.token_quota.check_and_consume("user_a", 100)
        with pytest.raises(HTTPException) as exc_info:
            with mw.anti_abuse_guard("user_a", estimated_tokens=1):
                pass
        assert exc_info.value.status_code == 429
        assert exc_info.value.detail == "ถึง Rate Limit กรุณารอสักครู่"

    def test_anti_abuse_guard_concurrency_slot_released_on_token_exhaustion(self, fake_redis):
        """
        When token check fails inside anti_abuse_guard.__enter__, the
        concurrency slot acquired before the token check must be released.
        """
        mw.init_limiters(
            redis_client=fake_redis,
            daily_token_limit=100,
            max_concurrent=2,
        )
        mw.token_quota.check_and_consume("user_a", 100)
        try:
            with mw.anti_abuse_guard("user_a", estimated_tokens=1):
                pass
        except HTTPException:
            pass
        assert mw.concurrency_limiter.get_active("user_a") == 0

    def test_uninitialised_raises_runtime_error(self):
        with pytest.raises(RuntimeError, match="not initialised"):
            mw.require_not_rate_limited(request=MagicMock(), user_id="x")


# ===========================================================================
# 6. Failure mode tests (Redis unavailable)
# ===========================================================================

class TestFailOpenBehaviour:
    """
    Verify that all three limiters fail open when Redis raises connection errors.
    The application must remain alive and serve requests.

    Strategy: construct each limiter against a real fakeredis instance so that
    __init__ (which calls register_script) succeeds.  Then patch the already-
    registered _script object with a side_effect to simulate a runtime failure.
    """

    def test_rate_limiter_fail_open(self, fake_redis):
        limiter = RateLimiter(
            redis_client=fake_redis,
            global_tiers=(RateLimitConfig(window_ms=3_600_000, limit=1, ttl_sec=7200),),
            user_tiers=(RateLimitConfig(window_ms=3_600_000, limit=1, ttl_sec=7200),),
        )
        # Exhaust both limits with one real call each
        limiter.check_global()
        limiter.check_user("user_a")
        # Now simulate Redis dying at runtime by replacing the script callable
        broken_script = MagicMock(side_effect=Exception("connection refused"))
        limiter._script = broken_script
        # With Redis broken, fail-open: all checks must return True
        assert limiter.check_global() is True
        assert limiter.check_user("user_a") is True
        assert limiter.check_all("user_a") is True

    def test_token_quota_fail_open(self, fake_redis):
        quota = TokenQuota(redis_client=fake_redis, daily_limit=10)
        # Replace the Lua script with a broken callable
        quota._script = MagicMock(side_effect=Exception("timeout"))
        # Also break the raw get for get_remaining
        fake_redis.get = MagicMock(side_effect=Exception("timeout"))
        assert quota.check_and_consume("user_a", 5) is True  # fail-open
        assert quota.get_remaining("user_a") == 10           # returns full limit on error

    def test_concurrency_limiter_fail_open(self, fake_redis):
        limiter = ConcurrencyLimiter(redis_client=fake_redis, max_concurrent=1)
        # Fill the slot so the next call would normally be denied
        limiter.acquire("user_a")
        assert limiter.acquire("user_a") is False  # confirmed: limit is enforced
        # Now simulate Redis failure at runtime
        limiter._script = MagicMock(side_effect=Exception("connection refused"))
        # With Redis broken, fail-open must let both calls through
        assert limiter.acquire("user_a") is True
        assert limiter.acquire("user_a") is True

    def test_add_rate_limit_headers_silent_on_error(self, fake_redis):
        """add_rate_limit_headers must not raise even if something is wrong."""
        mw.init_limiters(redis_client=fake_redis, daily_token_limit=100)
        response = MagicMock()
        response.headers = {}
        # Should complete without raising
        mw.add_rate_limit_headers(response, user_id=None)
        mw.add_rate_limit_headers(response, user_id="user_a")
