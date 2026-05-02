"""
locustfile.py — Locust load test for the anti_abuse test harness.

Three user classes model distinct traffic patterns:
    NormalUser  — steady 1 req/s to /ping (should stay within limits)
    BurstUser   — 10 req/s to /ping  (will saturate global 5 req/s limit → 429s)
    TokenUser   — 1 req/s to /llm    (will exhaust 500-token daily cap → 429s)

All 429 responses are validated to contain the canonical Thai payload:
    {"detail": "ถึง Rate Limit กรุณารอสักครู่"}

Run headless (no web UI):
    python -m locust -f tests/locustfile.py \
        --headless -u 15 -r 3 --run-time 20s \
        --host http://localhost:8099

Exit codes:
    0 — all tasks completed within Locust's failure thresholds
    1 — failures exceeded threshold (expected here because 429s are intentional)

Note: 429 responses are registered as successes (not failures) because they are
expected behaviour — the rate limiter is working correctly.  Genuine failures
are 5xx responses or connection errors.
"""

import json

from locust import HttpUser, between, task, events


# ---------------------------------------------------------------------------
# Payload validation helper
# ---------------------------------------------------------------------------

_EXPECTED_429_DETAIL = "ถึง Rate Limit กรุณารอสักครู่"


def _validate_429(response) -> None:
    """
    Assert that a 429 response body matches the canonical Thai detail string.
    Fires a Locust failure event if the body is malformed.
    """
    if response.status_code != 429:
        return
    try:
        body = response.json()
        detail = body.get("detail", "")
        if detail != _EXPECTED_429_DETAIL:
            response.failure(
                f"429 body mismatch — expected '{_EXPECTED_429_DETAIL}' "
                f"got '{detail}'"
            )
    except (json.JSONDecodeError, Exception) as exc:
        response.failure(f"429 response is not valid JSON: {exc}")


# ---------------------------------------------------------------------------
# User classes
# ---------------------------------------------------------------------------

class NormalUser(HttpUser):
    """
    Simulates a well-behaved user: 1 request per second to /ping.

    At 1 req/s per user and a global cap of 5 req/s, a single NormalUser
    should rarely hit the global limit.  With several NormalUsers running
    simultaneously some 429s are expected (global cap is intentionally tight
    for testing purposes).

    Locust weight=5 means 5 NormalUsers are spawned for every 1 BurstUser.
    """
    weight = 5
    wait_time = between(0.9, 1.1)  # ~1 req/s

    @task
    def ping(self):
        with self.client.get(
            "/ping",
            params={"user_id": f"normal_{self.user_id_suffix}"},
            catch_response=True,
            name="/ping [normal]",
        ) as resp:
            if resp.status_code in (200, 429):
                _validate_429(resp)
                resp.success()
            else:
                resp.failure(f"Unexpected status {resp.status_code}")

    @property
    def user_id_suffix(self) -> str:
        # Use Locust's internal ID so each user has a distinct user_id
        return str(id(self) % 1000)


class BurstUser(HttpUser):
    """
    Simulates an abusive client: fires ~10 req/s to /ping.

    Expect a high 429 rate — the global 5 req/s cap and per-user 10 req/min
    cap (= ~0.17 req/s sustained) will both be hit quickly.

    Locust weight=2 means 2 BurstUsers per spawn cycle.
    """
    weight = 2
    wait_time = between(0.08, 0.12)  # ~10 req/s

    @task
    def burst_ping(self):
        with self.client.get(
            "/ping",
            params={"user_id": f"burst_{id(self) % 100}"},
            catch_response=True,
            name="/ping [burst]",
        ) as resp:
            if resp.status_code in (200, 429):
                _validate_429(resp)
                resp.success()
            else:
                resp.failure(f"Unexpected status {resp.status_code}")


class TokenUser(HttpUser):
    """
    Simulates a user that calls the /llm endpoint at ~1 req/s consuming
    100 tokens per call.  With a 500-token daily cap the user will be
    blocked after 5 successful calls.

    Locust weight=3 means 3 TokenUsers per spawn cycle.
    """
    weight = 3
    wait_time = between(0.9, 1.1)  # ~1 req/s

    @task
    def llm_call(self):
        with self.client.get(
            "/llm",
            params={
                "user_id": f"token_{id(self) % 100}",
                "tokens": 100,
            },
            catch_response=True,
            name="/llm [token]",
        ) as resp:
            if resp.status_code == 200:
                try:
                    body = resp.json()
                    if body.get("status") != "ok":
                        resp.failure(f"Unexpected body: {body}")
                    else:
                        resp.success()
                except Exception as exc:
                    resp.failure(f"JSON decode error on 200: {exc}")
            elif resp.status_code == 429:
                _validate_429(resp)
                resp.success()
            else:
                resp.failure(f"Unexpected status {resp.status_code}")


# ---------------------------------------------------------------------------
# Event hooks — print a summary banner after the test run
# ---------------------------------------------------------------------------

@events.quitting.add_listener
def on_quitting(environment, **kwargs):
    stats = environment.runner.stats
    total = stats.total
    print("\n" + "=" * 60)
    print("LOCUST BENCHMARK SUMMARY")
    print("=" * 60)
    print(f"  Total requests   : {total.num_requests}")
    print(f"  Failures (5xx)   : {total.num_failures}")
    print(f"  Median latency   : {total.median_response_time} ms")
    print(f"  p95 latency      : {total.get_response_time_percentile(0.95)} ms")
    print(f"  p99 latency      : {total.get_response_time_percentile(0.99)} ms")
    print(f"  Avg RPS          : {total.total_rps:.2f}")
    print("=" * 60)
    # Per-endpoint breakdown
    for name, entry in stats.entries.items():
        r429 = entry.num_requests - entry.num_failures  # 429s counted as success
        print(
            f"  {name[1]:30s} | "
            f"req={entry.num_requests:5d} | "
            f"fail={entry.num_failures:4d} | "
            f"p50={int(entry.median_response_time):5d}ms | "
            f"p95={int(entry.get_response_time_percentile(0.95)):5d}ms"
        )
    print("=" * 60 + "\n")
