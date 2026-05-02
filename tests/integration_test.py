"""
integration_test.py — Real Redis integration tests for the anti_abuse layer.

These tests require a live Redis server at redis://localhost:6379/15.
All tests use DB 15 (isolated from any development data).

Test categories
---------------
1. Lua sliding-window atomicity under real concurrency
   - 50 threads all attempt to increment a key with limit=10.
   - Final ZCARD must be exactly 10 (no overrun, no undercount).

2. Token quota Lua prevents overrun under concurrency
   - 20 threads each attempt to consume 10 tokens against a cap of 100.
   - Final Redis value must be exactly 100 (cap not exceeded).

3. Bangkok midnight rollover (freezegun)
   - Freeze at 2026-04-02 16:59:58 UTC (= 23:59:58 Bangkok time).
   - Consume 4900 tokens.
   - Advance to 2026-04-02 17:00:01 UTC (= 00:00:01 Bangkok time, next day).
   - Verify a new Redis key is written and the counter starts from scratch.

4. Fail-open under Redis timeout
   - Force socket_timeout=0.001 so every Redis call times out immediately.
   - Verify check_global(), check_user(), check_and_consume() all return True.

Skip behaviour
--------------
The entire module is skipped gracefully if Redis is not reachable at startup,
so CI pipelines without Redis don't fail.
"""

import threading
import time

import pytest

# ---------------------------------------------------------------------------
# Redis availability check — skip module if not available
# ---------------------------------------------------------------------------

REDIS_URL = "redis://127.0.0.1:6379/15"
REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
REDIS_DB   = 15

try:
    import redis as _redis_lib

    _probe = _redis_lib.Redis(
        host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB,
        socket_timeout=3.0, retry_on_timeout=False, decode_responses=True,
    )
    _probe.ping()
    _probe.close()
    REDIS_AVAILABLE = True
except Exception:
    REDIS_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not REDIS_AVAILABLE,
    reason="Redis not available at redis://localhost:6379/15 — skipping integration tests",
)

# ---------------------------------------------------------------------------
# Only import the rest if Redis is available (avoids import-time errors when
# freezegun / redis are missing in some envs)
# ---------------------------------------------------------------------------

import redis
from freezegun import freeze_time

from anti_abuse.rate_limit import RateLimiter, RateLimitConfig
from anti_abuse.token_limit import TokenQuota, _token_key
from anti_abuse.concurrency import ConcurrencyLimiter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def flush_db():
    """
    Flush Redis DB 15 before AND after every test.

    autouse=True means this runs for every test in this module automatically.
    The pre-test flush guarantees a clean slate.
    The post-test flush leaves DB 15 empty for manual inspection after the suite.
    """
    client = redis.Redis(
        host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB,
        socket_timeout=3.0, retry_on_timeout=False, decode_responses=True,
    )
    client.flushdb()
    yield client
    client.flushdb()
    client.close()


@pytest.fixture
def real_redis(flush_db):
    """Yield the already-flushed real Redis client from the autouse fixture."""
    return flush_db


# ---------------------------------------------------------------------------
# Helper: create a RateLimiter with a single tight tier for atomicity tests
# ---------------------------------------------------------------------------

def _make_rate_limiter(client: redis.Redis, limit: int, window_ms: int = 60_000) -> RateLimiter:
    return RateLimiter(
        redis_client=client,
        global_tiers=(
            RateLimitConfig(window_ms=window_ms, limit=limit, ttl_sec=120),
        ),
        user_tiers=(
            RateLimitConfig(window_ms=window_ms, limit=limit, ttl_sec=120),
        ),
    )


# ---------------------------------------------------------------------------
# Test 1 — Sliding-window Lua atomicity under 50 concurrent threads
# ---------------------------------------------------------------------------

class TestSlidingWindowAtomicity:
    """
    Hammers check_global() with 50 threads against a limit of 10.
    The Lua script must guarantee exactly 10 entries in the sorted set —
    no more, no less.
    """

    def test_zcard_never_exceeds_limit(self, real_redis: redis.Redis):
        LIMIT = 10
        NUM_THREADS = 50
        limiter = _make_rate_limiter(real_redis, limit=LIMIT, window_ms=60_000)

        allowed_count = 0
        lock = threading.Lock()

        def worker():
            nonlocal allowed_count
            result = limiter.check_global()
            if result:
                with lock:
                    allowed_count += 1

        threads = [threading.Thread(target=worker) for _ in range(NUM_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # The sorted set key for the single global tier
        key = "rl:v1:global:req:60000ms"
        final_zcard = real_redis.zcard(key)

        print(f"\n[atomicity] allowed={allowed_count}, ZCARD={final_zcard}, LIMIT={LIMIT}")

        # The ZCARD must equal exactly LIMIT (10 entries admitted, no overrun)
        assert final_zcard == LIMIT, (
            f"ZCARD={final_zcard} != LIMIT={LIMIT} — Lua atomicity failure"
        )
        assert allowed_count == LIMIT, (
            f"allowed_count={allowed_count} != LIMIT={LIMIT}"
        )

    def test_per_user_zcard_never_exceeds_limit(self, real_redis: redis.Redis):
        LIMIT = 10
        NUM_THREADS = 50
        limiter = _make_rate_limiter(real_redis, limit=LIMIT, window_ms=60_000)

        allowed_count = 0
        lock = threading.Lock()
        user_id = "test_user_atomicity"

        def worker():
            nonlocal allowed_count
            result = limiter.check_user(user_id)
            if result:
                with lock:
                    allowed_count += 1

        threads = [threading.Thread(target=worker) for _ in range(NUM_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        key = f"rl:v1:user:{user_id}:req:60000ms"
        final_zcard = real_redis.zcard(key)

        print(f"\n[per-user atomicity] allowed={allowed_count}, ZCARD={final_zcard}, LIMIT={LIMIT}")

        assert final_zcard == LIMIT, (
            f"Per-user ZCARD={final_zcard} != LIMIT={LIMIT}"
        )
        assert allowed_count == LIMIT


# ---------------------------------------------------------------------------
# Test 2 — Token quota Lua prevents overrun under real concurrency
# ---------------------------------------------------------------------------

class TestTokenQuotaAtomicity:
    """
    20 threads each try to consume 10 tokens against a cap of 100.
    The Lua script must ensure the final Redis value is exactly 100 —
    not 110 or 120 (which would happen without atomicity).
    """

    def test_token_counter_never_exceeds_cap(self, real_redis: redis.Redis):
        CAP = 100
        TOKENS_PER_THREAD = 10
        NUM_THREADS = 20   # 20 × 10 = 200 tokens attempted; only first 10 threads succeed
        quota = TokenQuota(redis_client=real_redis, daily_limit=CAP)
        user_id = "test_user_token_atomic"

        allowed_count = 0
        lock = threading.Lock()

        def worker():
            nonlocal allowed_count
            result = quota.check_and_consume(user_id, TOKENS_PER_THREAD)
            if result:
                with lock:
                    allowed_count += 1

        threads = [threading.Thread(target=worker) for _ in range(NUM_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Retrieve the actual value from Redis
        from anti_abuse.token_limit import _bangkok_date_str
        date_str = _bangkok_date_str()
        key = _token_key(user_id, date_str)
        final_value = int(real_redis.get(key) or 0)

        print(
            f"\n[token atomicity] allowed={allowed_count}, "
            f"final_redis_value={final_value}, CAP={CAP}"
        )

        # The Redis value must equal exactly CAP — the Lua script must prevent overrun
        assert final_value == CAP, (
            f"Token counter={final_value} != CAP={CAP} — Lua atomicity failure"
        )
        assert allowed_count == CAP // TOKENS_PER_THREAD, (
            f"allowed_count={allowed_count} != {CAP // TOKENS_PER_THREAD}"
        )

    def test_token_key_name_matches_schema(self, real_redis: redis.Redis):
        """Verify the Redis key name follows the documented schema."""
        quota = TokenQuota(redis_client=real_redis, daily_limit=100)
        user_id = "schema_check_user"

        quota.check_and_consume(user_id, 10)

        from anti_abuse.token_limit import _bangkok_date_str
        date_str = _bangkok_date_str()
        expected_key = f"tl:v1:user:{user_id}:tokens:{date_str}"

        all_keys = real_redis.keys("tl:*")
        print(f"\n[schema] Keys in DB: {all_keys}")
        assert expected_key in all_keys, (
            f"Expected key '{expected_key}' not found. Keys present: {all_keys}"
        )


# ---------------------------------------------------------------------------
# Test 3 — Bangkok midnight rollover with freezegun
# ---------------------------------------------------------------------------

class TestBangkokMidnightRollover:
    """
    Verifies that the quota resets at Bangkok midnight by checking that a new
    Redis key is created with a different date suffix.

    Timeline:
        16:59:58 UTC = 23:59:58 Bangkok (2026-04-02 Bangkok date)
        17:00:01 UTC = 00:00:01 Bangkok (2026-04-03 Bangkok date)
    """

    def test_new_key_created_after_midnight(self, real_redis: redis.Redis):
        CAP = 5_000
        quota = TokenQuota(redis_client=real_redis, daily_limit=CAP)
        user_id = "rollover_test_user"

        # ----------------------------------------------------------------
        # Phase 1: 23:59:58 Bangkok time — consume 4900 tokens
        # ----------------------------------------------------------------
        with freeze_time("2026-04-02 16:59:58"):  # UTC → Bangkok = 23:59:58
            allowed = quota.check_and_consume(user_id, 4900)
            assert allowed, "Initial 4900-token consume should be allowed"

            from anti_abuse.token_limit import _bangkok_date_str
            date_before = _bangkok_date_str()
            key_before = _token_key(user_id, date_before)

            value_before = int(real_redis.get(key_before) or 0)
            print(f"\n[rollover] Before midnight: key={key_before}, value={value_before}")
            assert value_before == 4900
            assert date_before == "2026-04-02", f"Expected 2026-04-02, got {date_before}"

        # ----------------------------------------------------------------
        # Phase 2: 00:00:01 Bangkok time (next day) — consume 100 tokens
        # ----------------------------------------------------------------
        with freeze_time("2026-04-02 17:00:01"):  # UTC → Bangkok = 00:00:01 on 2026-04-03
            from anti_abuse.token_limit import _bangkok_date_str as _bds_new
            date_after = _bds_new()
            key_after = _token_key(user_id, date_after)

            print(f"[rollover] After midnight: expected new key={key_after}")
            assert date_after == "2026-04-03", f"Expected 2026-04-03, got {date_after}"
            assert key_after != key_before, "Key must differ after midnight rollover"

            # New key should not exist yet (quota is fresh)
            raw_new = real_redis.get(key_after)
            assert raw_new is None, (
                f"New day's key already exists with value {raw_new} — unexpected"
            )

            # Consuming 100 tokens on the new day should succeed
            allowed_new = quota.check_and_consume(user_id, 100)
            assert allowed_new, "100-token consume on new day should be allowed"

            value_after = int(real_redis.get(key_after) or 0)
            print(f"[rollover] After consume on new day: key={key_after}, value={value_after}")
            assert value_after == 100

        # ----------------------------------------------------------------
        # Report actual Redis key names
        # ----------------------------------------------------------------
        all_keys = real_redis.keys("tl:*")
        print(f"[rollover] All token keys in DB 15: {sorted(all_keys)}")

    def test_quota_exhaustion_then_rollover(self, real_redis: redis.Redis):
        """Exhaust the quota just before midnight, confirm fresh quota after."""
        CAP = 200
        quota = TokenQuota(redis_client=real_redis, daily_limit=CAP)
        user_id = "exhaust_rollover_user"

        with freeze_time("2026-04-02 16:59:50"):
            # Exhaust the quota
            assert quota.check_and_consume(user_id, 200)
            # This should be denied
            denied = quota.check_and_consume(user_id, 1)
            assert not denied, "Should be denied after quota exhaustion"

        with freeze_time("2026-04-02 17:00:05"):
            # After midnight — quota resets to fresh key
            allowed = quota.check_and_consume(user_id, 200)
            assert allowed, "Full quota should be available on new Bangkok day"


# ---------------------------------------------------------------------------
# Test 4 — Fail-open when Redis times out
# ---------------------------------------------------------------------------

class TestFailOpen:
    """
    Forces Redis to time out (socket_timeout=0.001s) and verifies that all
    limiters fail-open (return True / allow the request through).
    """

    def _make_timeout_client(self) -> redis.Redis:
        """
        Create a Redis client with an absurdly small timeout.
        The connection itself should succeed (Redis is running), but any
        command that touches the network will time out.
        """
        return redis.Redis(
            host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB,
            socket_timeout=0.001,        # 1ms — intentionally too small
            retry_on_timeout=False,      # no retries; fail immediately
            decode_responses=True,
        )

    def test_rate_limiter_fails_open(self, real_redis: redis.Redis):
        """RateLimiter.check_global() must return True on Redis error."""
        # Prime the connection so connect itself succeeds, then swap to tiny timeout
        slow_client = self._make_timeout_client()
        limiter = RateLimiter(
            redis_client=slow_client,
            global_tiers=(RateLimitConfig(window_ms=1_000, limit=1, ttl_sec=5),),
            user_tiers=(RateLimitConfig(window_ms=60_000, limit=1, ttl_sec=120),),
        )

        # With a 1ms timeout the Lua execution will time out — must fail-open
        result = limiter.check_global()
        assert result is True, "RateLimiter.check_global() must fail-open on Redis timeout"

    def test_token_quota_fails_open(self, real_redis: redis.Redis):
        """TokenQuota.check_and_consume() must return True on Redis error."""
        slow_client = self._make_timeout_client()
        quota = TokenQuota(redis_client=slow_client, daily_limit=100)

        result = quota.check_and_consume("timeout_user", 50)
        assert result is True, "TokenQuota.check_and_consume() must fail-open on Redis timeout"

    def test_concurrency_limiter_fails_open(self, real_redis: redis.Redis):
        """ConcurrencyLimiter.acquire() must return True on Redis error."""
        slow_client = self._make_timeout_client()
        gate = ConcurrencyLimiter(redis_client=slow_client, max_concurrent=1)

        result = gate.acquire("timeout_user")
        assert result is True, "ConcurrencyLimiter.acquire() must fail-open on Redis timeout"

    def test_per_user_rate_limiter_fails_open(self, real_redis: redis.Redis):
        """RateLimiter.check_user() must return True on Redis error."""
        slow_client = self._make_timeout_client()
        limiter = RateLimiter(
            redis_client=slow_client,
            global_tiers=(),
            user_tiers=(RateLimitConfig(window_ms=60_000, limit=1, ttl_sec=120),),
        )

        result = limiter.check_user("timeout_user")
        assert result is True, "RateLimiter.check_user() must fail-open on Redis timeout"


# ---------------------------------------------------------------------------
# Test 5 — Redis key schema spot-check
# ---------------------------------------------------------------------------

class TestRedisKeySchema:
    """
    Verifies that the actual Redis keys written match the documented schema.
    This catches any accidental key-format regressions.

    Expected patterns:
        rl:v1:global:req:{window_ms}ms
        rl:v1:user:{uid}:req:{window_ms}ms
        tl:v1:user:{uid}:tokens:{YYYY-MM-DD}
        cc:v1:user:{uid}:active
    """

    def test_rate_limit_global_key_schema(self, real_redis: redis.Redis):
        limiter = _make_rate_limiter(real_redis, limit=100, window_ms=60_000)
        limiter.check_global()
        keys = real_redis.keys("rl:v1:global:*")
        assert any("60000ms" in k for k in keys), (
            f"Global 60s key not found. Keys: {keys}"
        )

    def test_rate_limit_user_key_schema(self, real_redis: redis.Redis):
        limiter = _make_rate_limiter(real_redis, limit=100, window_ms=60_000)
        limiter.check_user("schema_uid_123")
        keys = real_redis.keys("rl:v1:user:schema_uid_123:*")
        assert any("60000ms" in k for k in keys), (
            f"Per-user 60s key not found. Keys: {keys}"
        )

    def test_token_key_schema(self, real_redis: redis.Redis):
        quota = TokenQuota(redis_client=real_redis, daily_limit=1000)
        quota.check_and_consume("schema_uid_456", 10)
        keys = real_redis.keys("tl:v1:user:schema_uid_456:*")
        assert len(keys) == 1, f"Expected 1 token key, got: {keys}"
        assert keys[0].startswith("tl:v1:user:schema_uid_456:tokens:"), (
            f"Key format wrong: {keys[0]}"
        )

    def test_concurrency_key_schema(self, real_redis: redis.Redis):
        gate = ConcurrencyLimiter(redis_client=real_redis, max_concurrent=5)
        gate.acquire("schema_uid_789")
        keys = real_redis.keys("cc:v1:user:schema_uid_789:*")
        assert len(keys) == 1, f"Expected 1 concurrency key, got: {keys}"
        assert keys[0] == "cc:v1:user:schema_uid_789:active", (
            f"Concurrency key format wrong: {keys[0]}"
        )

    def test_all_key_schemas_together(self, real_redis: redis.Redis):
        """Trigger all key types at once and report every key written."""
        limiter = _make_rate_limiter(real_redis, limit=100, window_ms=60_000)
        quota = TokenQuota(redis_client=real_redis, daily_limit=1000)
        gate = ConcurrencyLimiter(redis_client=real_redis, max_concurrent=5)
        uid = "combined_schema_user"

        limiter.check_global()
        limiter.check_user(uid)
        quota.check_and_consume(uid, 50)
        gate.acquire(uid)

        all_keys = sorted(real_redis.keys("*"))
        print(f"\n[schema] All keys after combined test: {all_keys}")

        assert any(k.startswith("rl:v1:global:") for k in all_keys)
        assert any(k.startswith(f"rl:v1:user:{uid}:") for k in all_keys)
        assert any(k.startswith(f"tl:v1:user:{uid}:") for k in all_keys)
        assert any(k.startswith(f"cc:v1:user:{uid}:") for k in all_keys)
