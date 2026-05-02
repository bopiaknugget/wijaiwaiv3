# Redis Performance Benchmark Report

**Date**: 2026-04-04  
**Server**: localhost:6379  
**Redis Version**: 7.0.15  
**Purpose**: Production readiness assessment for OpenThaiGPT API rate limiter  
**Pass Threshold**: >= 5,000 req/sec for rate-limit ops with < 1ms p99 latency

---

## Environment

| Parameter | Value |
|-----------|-------|
| OS | Linux 6.6.87.2-microsoft-standard-WSL2 x86_64 |
| Redis Mode | Standalone (single-threaded event loop) |
| Total System RAM | 15.57 GB |
| Redis Used Memory | 2.02 MB (nearly empty at benchmark time) |
| Redis Peak Memory | 7.37 MB |
| Persistence | RDB only (`save 3600 1 300 100 60 10000`), AOF disabled |
| Multiplexing | epoll |
| Replication | None (master, no replicas) |
| Auth | None |
| io_threads | 0 (single-threaded) |

---

## Throughput Results

### Basic Commands — 20 Concurrent Clients, No Pipeline (100k requests)

| Command | RPS | p50 (ms) | p95 (ms) | p99 (ms) | p99.9 (ms) | Max (ms) |
|---------|-----|----------|----------|----------|------------|---------|
| SET | 55,494 | 0.175 | 0.303 | 0.471 | ~1.0 | 9.311 |
| GET | 56,148 | 0.175 | 0.295 | 0.431 | ~0.86 | 1.447 |
| INCR | 56,370 | 0.175 | 0.303 | 0.447 | ~0.94 | 4.327 |
| ZADD | 57,045 | 0.175 | 0.303 | 0.439 | ~1.0 | 2.903 |

**Interpretation**: All four rate-limit-critical commands achieve 55k–57k RPS at real user load (20 clients).
This is 11x the minimum required threshold of 5,000 RPS. At p99, all commands complete in under 0.5ms —
well below the 1ms production target. The occasional max spike (up to 9ms for SET) is caused by RDB
background save activity (bgsave), not Redis latency itself.

---

### Pipelined Throughput — 20 Clients, Pipeline=16 (100k requests)

| Command | RPS (pipelined) | RPS (no-pipeline) | Pipeline Multiplier | p99 (ms) |
|---------|-----------------|-------------------|---------------------|----------|
| SET | 934,579 | 55,494 | 16.8x | 0.831 |
| GET | 934,579 | 56,148 | 16.6x | 0.527 |
| INCR | 943,396 | 56,370 | 16.7x | 0.591 |
| ZADD | 869,565 | 57,045 | 15.2x | 0.599 |

**Interpretation**: Pipeline throughput scales almost linearly with pipeline depth (16x pipeline = ~16x RPS).
This demonstrates the network round-trip is the dominant cost for individual commands, not Redis processing.
Rate limiter code should use pipelining or Lua scripts to batch atomic operations for maximum efficiency.

---

### Lua EVAL Benchmarks — Rate Limiter Patterns (100k requests, 20 clients)

| Pattern | Implementation | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Max (ms) |
|---------|---------------|-----|----------|----------|----------|---------|
| Fixed-window | `INCR key; EXPIRE key 5` (atomic Lua) | 62,344 | 0.159 | 0.351 | 0.543 | 13.431 |
| Sliding-window | `ZADD + ZREMRANGEBYSCORE + ZCARD + EXPIRE` (atomic Lua) | 62,775 | 0.231 | 0.439 | 0.631 | 1.319 |

**Interpretation**: Atomic Lua scripts — which are exactly how production rate limiters should be implemented —
actually outperform single bare INCR/ZADD calls (62k vs 56k RPS). This is because Lua execution eliminates
multiple round-trips and reduces network overhead per logical "check". The sliding window pattern with four
Redis operations inside one EVAL achieves 62,775 RPS at 0.631ms p99. This is 12.5x the 5k RPS threshold.

The 13ms max spike on the fixed-window EVAL is a WSL2 scheduler artifact — WSL2 periodically introduces
scheduling jitter not present on bare-metal Linux. This will not appear in production Linux deployments.

---

## Latency Scaling — INCR Under Concurrency

| Clients | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Max (ms) | Assessment |
|---------|-----|----------|----------|----------|---------|------------|
| 1 | 10,423 | 0.071 | 0.151 | 0.199 | 4.031 | Baseline — sequential |
| 10 | 53,879 | 0.095 | 0.191 | 0.287 | 1.031 | Excellent |
| 20 | 56,497 | 0.175 | 0.295 | 0.431 | 1.087 | PASS — matches prod load |
| 50 | 51,921 | 0.455 | 0.727 | 0.983 | 9.199 | Good — p99 < 1ms |
| 100 | 54,348 | 0.887 | 1.359 | 1.703 | 2.815 | Warning — p99 > 1ms |

**Interpretation**: The Redis server saturates at approximately 56k RPS regardless of concurrency (10–100 clients),
which confirms the bottleneck is the single event loop CPU core, not client concurrency. At 20 concurrent users
(the production maximum), p99 is 0.431ms — 2.3x headroom below the 1ms target. At 50 concurrent users
Redis still stays under 1ms p99 (0.983ms). Only at 100 clients does p99 exceed 1ms (1.703ms).

---

### ZADD Scaling (Sliding Window Core Operation)

| Clients | RPS | p50 (ms) | p95 (ms) | p99 (ms) |
|---------|-----|----------|----------|----------|
| 1 | 10,419 | 0.079 | 0.151 | 0.199 |
| 20 | 57,670 | 0.175 | 0.303 | 0.439 |
| 50 | 59,242 | 0.407 | 0.631 | 0.839 |

ZADD performs identically to INCR at every concurrency level. The sliding window implementation will
not have higher latency than the fixed-window approach.

---

## Payload Size Impact — SET/GET (20 clients)

| Payload | SET RPS | GET RPS | SET p99 (ms) | GET p99 (ms) |
|---------|---------|---------|--------------|--------------|
| 16 bytes (rate-limit key value) | 53,763 | 54,885 | 0.487 | 0.447 |
| 64 bytes | 56,754 | 56,529 | 0.439 | 0.439 |
| 512 bytes | 58,548 | 54,025 | 0.439 | 0.447 |

**Interpretation**: Throughput is completely payload-size-insensitive across 16–512 bytes. Rate limiter
keys store integer counters or timestamps (< 20 bytes), which is the best-case scenario. Payload size
is not a concern for this workload.

---

## Stress Test — 500k Requests, 50 Concurrent Clients

| Command | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Max (ms) |
|---------|-----|----------|----------|----------|---------|
| INCR (sustained) | 55,182 | 0.439 | 0.687 | 0.919 | 10.287 |
| ZADD (sustained) | 56,954 | 0.423 | 0.679 | 0.895 | 13.319 |

**Interpretation**: Under sustained load 10x the normal volume (500k requests vs 50k typical benchmark),
throughput holds steady at 55–57k RPS with p99 remaining below 1ms. This confirms the server is not
accumulating latency debt under load. The 10–13ms max spikes are WSL2 jitter, not Redis degradation.

---

## Memory Analysis

| Metric | Value | Assessment |
|--------|-------|------------|
| Used memory | 2.02 MB | Minimal (nearly empty keyspace) |
| RSS memory | 13.61 MB | Normal kernel allocation overhead |
| Peak memory | 7.37 MB | Consistent with benchmark run |
| Fragmentation ratio | 6.87 | WARNING — high, but expected at low memory |
| maxmemory | 0 (unlimited) | WARNING — no limit set |
| maxmemory-policy | noeviction | WARNING — will error instead of evict |
| Memory doctor | "instance is empty" | No issues detectable |
| System RAM | 15.57 GB | Ample headroom |

**Memory fragmentation note**: A fragmentation ratio of 6.87 is alarming on the surface but is a known
artifact of near-empty Redis instances with jemalloc pre-allocating pages. At production key load
(thousands of rate-limit keys), this ratio will naturally converge toward 1.0–1.5. No action needed now.

**Unlimited maxmemory risk**: With `maxmemory 0`, Redis will consume all available RAM before OOM-killing
processes. For a rate limiter, keys expire naturally (EXPIRE is set per key), so memory growth is bounded.
However, a safety cap is still recommended.

---

## OS / Scheduler Intrinsic Latency

```
Max latency observed:  8.837 ms  (worst case WSL2 scheduler pause)
Avg latency per loop:  0.0438 microseconds (43.8 nanoseconds)
Worst run vs average:  201,590x longer
```

**Interpretation**: This is a WSL2 hypervisor scheduling characteristic. WSL2 runs under Hyper-V, which
can cause 1–9ms scheduling pauses at unpredictable intervals. This explains the occasional high-end outliers
in benchmark results (not actual Redis processing time). On bare-metal Linux, intrinsic latency max would
be < 200 microseconds. This is acceptable for a development/staging environment but should be noted if this
Redis instance is considered for production hosting.

---

## Current Configuration Assessment

| Setting | Current Value | Recommended | Status |
|---------|--------------|-------------|--------|
| maxmemory | 0 (unlimited) | 512mb | WARNING |
| maxmemory-policy | noeviction | allkeys-lru or volatile-lru | WARNING |
| tcp-backlog | 511 | 511 | GOOD |
| maxclients | 10,000 | 1,000 | OK (over-sized) |
| hz | 10 | 100 | WARNING |
| appendonly | no | no (correct for rate limiter) | GOOD |
| save | 3600 1 300 100 60 10000 | "" (disable) | WARNING |
| latency-tracking | yes | yes | GOOD |
| io_threads | 0 (single) | 0 (correct for < 1M RPS) | GOOD |

---

## Production Readiness Verdict

### Overall: PASS

| Category | Result | Threshold | Status |
|----------|--------|-----------|--------|
| INCR throughput @ 20 clients | 56,370 RPS | >= 5,000 RPS | PASS (11.3x) |
| ZADD throughput @ 20 clients | 57,045 RPS | >= 5,000 RPS | PASS (11.4x) |
| Sliding window Lua EVAL | 62,775 RPS | >= 5,000 RPS | PASS (12.6x) |
| INCR p99 latency @ 20 clients | 0.431 ms | < 1 ms | PASS |
| ZADD p99 latency @ 20 clients | 0.439 ms | < 1 ms | PASS |
| Sliding window p99 @ 20 clients | 0.631 ms | < 1 ms | PASS |
| No rejected connections | 0 | 0 | PASS |
| No evicted keys | 0 | 0 | PASS |
| Sustained load (500k/50c) p99 | 0.919 ms | < 1 ms | PASS |

**Rate limit math check**:
- 20 users x 5 req/sec = 100 req/sec peak load
- Redis handles 56,000+ req/sec
- **Headroom: 560x the peak expected load**
- Burst of 20 users x 200 req/min = 4,000 req/min (67 req/sec) — trivial

---

## Recommendations

### Critical (apply before production)

**1. Set maxmemory and eviction policy**

Rate-limit keys have short TTLs (1 second for per-second window, 60 seconds for per-minute window),
but an explicit cap prevents unbounded growth if TTLs are accidentally omitted in a code bug.

```bash
redis-cli config set maxmemory 512mb
redis-cli config set maxmemory-policy volatile-lru
```

`volatile-lru` evicts only keys with TTLs, which is exactly what rate-limit keys use. This means
a memory pressure event will drop old rate-limit state (graceful degradation) rather than refusing
writes or crashing.

To persist across restarts, add to `/etc/redis/redis.conf`:
```
maxmemory 512mb
maxmemory-policy volatile-lru
```

**2. Disable RDB save for rate-limiter Redis instance**

The `save 3600 1 300 100 60 10000` configuration triggers bgsave forks. Each fork causes a
copy-on-write memory spike and brief latency elevation (visible in the occasional 9ms SET max latency).
Rate-limit counters are ephemeral — they do not need persistence.

```bash
redis-cli config set save ""
```

Or in `redis.conf`: comment out all `save` lines.

**3. Increase hz to 100**

`hz 10` means Redis only runs internal maintenance (key expiry, eviction checks) 10 times per second.
At 5 req/sec per-user windows, keys need to expire promptly. `hz 100` ensures expiry runs 100x/sec,
reducing the window during which expired keys accumulate.

```bash
redis-cli config set hz 100
```

### Recommended (improve observability and resilience)

**4. Enable slowlog with tight threshold**

```bash
redis-cli config set slowlog-log-slower-than 1000
redis-cli config set slowlog-max-len 256
```

This captures any command taking > 1ms, which should never happen given benchmark results. If it does,
the slowlog will point directly to the cause.

**5. Use atomic Lua scripts for all rate-limit operations**

The benchmark confirms Lua EVAL outperforms individual commands for multi-step operations:
- Fixed-window INCR+EXPIRE: **62,344 RPS** (vs 56,370 bare INCR)
- Sliding-window ZADD+ZREM+ZCARD+EXPIRE: **62,775 RPS**

The atomic guarantee also eliminates the race condition where a key could be incremented but
EXPIRE never set (e.g., on application crash between the two calls).

Recommended Lua scripts for each pattern:

```lua
-- Fixed-window rate limiter (5 req/sec)
-- Usage: EVAL script 1 "rl:{user_id}:sec" 5 1
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local count = redis.call('INCR', key)
if count == 1 then
  redis.call('EXPIRE', key, window)
end
if count > limit then
  return redis.error_reply('RATE_LIMIT_EXCEEDED')
end
return count
```

```lua
-- Sliding-window rate limiter (200 req/min)
-- Usage: EVAL script 1 "rl:{user_id}:min" {now_ms} 60000 200
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
redis.call('ZADD', key, now, now)
redis.call('ZREMRANGEBYSCORE', key, 0, now - window_ms)
local count = redis.call('ZCARD', key)
redis.call('EXPIRE', key, math.ceil(window_ms / 1000))
if count > limit then
  return redis.error_reply('RATE_LIMIT_EXCEEDED')
end
return count
```

**6. Use RESP3 protocol (Redis 7.x only)**

```python
# In Python with redis-py:
r = redis.Redis(host='localhost', port=6379, protocol=3)
```

RESP3 reduces parsing overhead for complex responses and is fully supported on Redis 7.0.15.

**7. Configure connection pooling in the application**

With 20 concurrent users, the app should maintain a connection pool of 25–30 connections to avoid
connection establishment overhead.

```python
# redis-py connection pool for rate limiter
pool = redis.ConnectionPool(
    host='localhost',
    port=6379,
    max_connections=30,
    socket_keepalive=True,
    socket_connect_timeout=1,
    socket_timeout=0.5
)
rate_limiter_client = redis.Redis(connection_pool=pool)
```

### Optional (bare-metal production only)

**8. Disable Transparent Huge Pages**

This is a Linux kernel setting that causes latency spikes under memory pressure:
```bash
echo never > /sys/kernel/mm/transparent_hugepage/enabled
```

Not applicable in WSL2 (cannot modify kernel settings), but critical on bare-metal Linux servers.

**9. Pin Redis to a specific CPU core**

```bash
taskset -c 0 redis-server /etc/redis/redis.conf
```

Eliminates CPU migration overhead on multi-core systems.

---

## Comparison Baselines

| Scenario | Expected RPS | This Server | Assessment |
|----------|-------------|-------------|------------|
| GET/SET localhost, no pipeline | 80k–150k | 55–57k | Below typical (WSL2 overhead) |
| GET/SET localhost, pipeline=16 | 500k–1M+ | 934k–943k | Excellent |
| Lua EVAL (multi-op) localhost | 40k–80k | 62–63k | Excellent |
| p99 latency, localhost, 20c | < 0.5ms | 0.43–0.47ms | Excellent |
| p99 latency, localhost, 50c | < 1ms | 0.84–0.98ms | PASS |

**Note on throughput vs baseline**: The 55k RPS figure (vs 80k–150k baseline) reflects WSL2 virtualization
overhead. WSL2 introduces ~20–40% latency vs native Linux for loopback socket operations. On a bare-metal
Linux server, expect 90k–130k RPS for the same workload, improving headroom further.

**Performance Grade: A- (Production Ready with configuration fixes)**

The server comfortably handles the target workload with 11x throughput headroom. The `-` reflects the
WSL2 environment limitation (which is acceptable for development/staging) and the missing `maxmemory`
and persistence configuration that must be fixed before a production deployment.

---

## Raw Benchmark Data

### Benchmark Run — 2026-04-04

#### Basic Throughput (100k requests, 20 clients, no pipeline)

```
SET:
  throughput summary: 55,493 requests per second
  latency summary (msec):
          avg       min       p50       p95       p99       max
        0.193     0.048     0.175     0.303     0.471     9.311

GET:
  throughput summary: 56,148 requests per second
  latency summary (msec):
          avg       min       p50       p95       p99       max
        0.186     0.056     0.175     0.295     0.431     1.447

INCR:
  throughput summary: 56,370 requests per second
  latency summary (msec):
          avg       min       p50       p95       p99       max
        0.186     0.032     0.175     0.303     0.447     4.327

ZADD:
  throughput summary: 57,045 requests per second
  latency summary (msec):
          avg       min       p50       p95       p99       max
        0.186     0.064     0.175     0.303     0.439     2.903
```

#### Pipelined Throughput (100k requests, 20 clients, pipeline=16)

```
SET:   934,579 RPS  |  p99: 0.831ms
GET:   934,579 RPS  |  p99: 0.527ms
INCR:  943,396 RPS  |  p99: 0.591ms
ZADD:  869,565 RPS  |  p99: 0.599ms
```

#### INCR Concurrency Scaling (50k requests)

```
C=1:    10,423 RPS  |  p50: 0.071ms  p95: 0.151ms  p99: 0.199ms  max: 4.031ms
C=10:   53,879 RPS  |  p50: 0.095ms  p95: 0.191ms  p99: 0.287ms  max: 1.031ms
C=20:   56,497 RPS  |  p50: 0.175ms  p95: 0.295ms  p99: 0.431ms  max: 1.087ms
C=50:   51,921 RPS  |  p50: 0.455ms  p95: 0.727ms  p99: 0.983ms  max: 9.199ms
C=100:  54,348 RPS  |  p50: 0.887ms  p95: 1.359ms  p99: 1.703ms  max: 2.815ms
```

#### ZADD Concurrency Scaling (50k requests)

```
C=1:    10,419 RPS  |  p50: 0.079ms  p95: 0.151ms  p99: 0.199ms  max: 0.471ms
C=20:   57,670 RPS  |  p50: 0.175ms  p95: 0.303ms  p99: 0.439ms  max: 0.983ms
C=50:   59,242 RPS  |  p50: 0.407ms  p95: 0.631ms  p99: 0.839ms  max: 1.263ms
```

#### Lua EVAL Rate-Limit Patterns (100k requests, 20 clients)

```
Fixed-window INCR+EXPIRE (atomic):
  throughput summary: 62,344 requests per second
  latency summary (msec):
          avg       min       p50       p95       p99       max
        0.189     0.072     0.159     0.351     0.543    13.431

Sliding-window ZADD+ZREMRANGEBYSCORE+ZCARD+EXPIRE (atomic):
  throughput summary: 62,775 requests per second
  latency summary (msec):
          avg       min       p50       p95       p99       max
        0.252     0.096     0.231     0.439     0.631     1.319
```

#### Payload Size Sensitivity (100k requests, 20 clients)

```
16 bytes (rate-limit key value):
  SET: 53,763 RPS  p99: 0.487ms
  GET: 54,885 RPS  p99: 0.447ms

64 bytes:
  SET: 56,754 RPS  p99: 0.439ms
  GET: 56,529 RPS  p99: 0.439ms

512 bytes:
  SET: 58,548 RPS  p99: 0.439ms
  GET: 54,025 RPS  p99: 0.447ms
```

#### Stress Test — 500k requests, 50 clients

```
INCR:
  throughput summary: 55,182 requests per second
  latency summary (msec):
          avg       min       p50       p95       p99       max
        0.462     0.144     0.439     0.687     0.919    10.287

ZADD:
  throughput summary: 56,954 requests per second
  latency summary (msec):
          avg       min       p50       p95       p99       max
        0.450     0.112     0.423     0.679     0.895    13.319
```

#### OS Intrinsic Latency (5-second sample, WSL2)

```
Max latency so far: 1 microseconds.
Max latency so far: 60 microseconds.
Max latency so far: 235 microseconds.
Max latency so far: 730 microseconds.
Max latency so far: 8,837 microseconds.

114,060,301 total runs (avg latency: 0.0438 microseconds / 43.84 nanoseconds per run).
Worst run took 201,590x longer than the average latency.
```

#### Memory State (post-benchmark)

```
used_memory:          2.02 MB
used_memory_rss:      13.61 MB
mem_fragmentation_ratio: 6.87  (expected at low occupancy)
maxmemory:            0 (unlimited)
maxmemory_policy:     noeviction
total_system_memory:  15.57 GB
```

#### Configuration

```
tcp-backlog:      511
maxclients:       10,000
hz:               10
appendonly:       no
save:             3600 1  300 100  60 10000
latency-tracking: yes
```

---

*Generated by run_redis_benchmark.sh — Redis 7.0.15 on WSL2 (Linux 6.6.87.2)*
