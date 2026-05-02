#!/usr/bin/env bash
# =============================================================================
# Redis Benchmark Suite — Research Workbench Rate Limiter
# Target: localhost:6379 (WSL2, Redis 7.0.x)
# Use case: Rate limiting for OpenThaiGPT API (5 req/sec, 200 req/min per user)
# =============================================================================
# Usage:
#   chmod +x run_redis_benchmark.sh
#   ./run_redis_benchmark.sh [host] [port]
#   Example: ./run_redis_benchmark.sh localhost 6379
# =============================================================================

HOST="${1:-localhost}"
PORT="${2:-6379}"
RESULTS_DIR="$(cd "$(dirname "$0")" && pwd)"
RESULTS_FILE="$RESULTS_DIR/redis_benchmark_results.md"
TIMESTAMP="$(date '+%Y-%m-%d %H:%M:%S')"

echo "Redis Benchmark Suite"
echo "Host: $HOST:$PORT"
echo "Results: $RESULTS_FILE"
echo "Started: $TIMESTAMP"
echo ""

# Pre-flight check
if ! redis-cli -h "$HOST" -p "$PORT" ping | grep -q PONG; then
  echo "ERROR: Cannot reach Redis at $HOST:$PORT"
  exit 1
fi

REDIS_VERSION=$(redis-cli -h "$HOST" -p "$PORT" info server | grep redis_version | tr -d '\r' | cut -d: -f2)
echo "Redis version: $REDIS_VERSION"

# ============================================================
# SECTION 1: Pre-flight server info
# ============================================================
echo ""
echo "[1/7] Collecting server info..."
SERVER_INFO=$(redis-cli -h "$HOST" -p "$PORT" info server 2>&1)
MEMORY_INFO=$(redis-cli -h "$HOST" -p "$PORT" info memory 2>&1)
CONFIG_MAXMEMORY=$(redis-cli -h "$HOST" -p "$PORT" config get maxmemory 2>&1)
CONFIG_POLICY=$(redis-cli -h "$HOST" -p "$PORT" config get maxmemory-policy 2>&1)
CONFIG_BACKLOG=$(redis-cli -h "$HOST" -p "$PORT" config get tcp-backlog 2>&1)
CONFIG_MAXCLIENTS=$(redis-cli -h "$HOST" -p "$PORT" config get maxclients 2>&1)
CONFIG_HZ=$(redis-cli -h "$HOST" -p "$PORT" config get hz 2>&1)
CONFIG_SAVE=$(redis-cli -h "$HOST" -p "$PORT" config get save 2>&1)
CONFIG_AOF=$(redis-cli -h "$HOST" -p "$PORT" config get appendonly 2>&1)

# ============================================================
# SECTION 2: Basic throughput — no pipeline, 20 clients
# ============================================================
echo "[2/7] Basic throughput (20 clients, no pipeline)..."
BM_BASIC_SET=$(redis-benchmark -h "$HOST" -p "$PORT" -t set -n 100000 -c 20 2>&1 | grep -E "throughput summary|latency summary|avg.*min.*p50|^\s+[0-9]")
BM_BASIC_GET=$(redis-benchmark -h "$HOST" -p "$PORT" -t get -n 100000 -c 20 2>&1 | grep -E "throughput summary|latency summary|avg.*min.*p50|^\s+[0-9]")
BM_BASIC_INCR=$(redis-benchmark -h "$HOST" -p "$PORT" -t incr -n 100000 -c 20 2>&1 | grep -E "throughput summary|latency summary|avg.*min.*p50|^\s+[0-9]")
BM_BASIC_ZADD=$(redis-benchmark -h "$HOST" -p "$PORT" -t zadd -n 100000 -c 20 2>&1 | grep -E "throughput summary|latency summary|avg.*min.*p50|^\s+[0-9]")

# ============================================================
# SECTION 3: Pipelined throughput — pipeline=16, 20 clients
# ============================================================
echo "[3/7] Pipelined throughput (20 clients, pipeline=16)..."
BM_PIPE_SET=$(redis-benchmark -h "$HOST" -p "$PORT" -t set -n 100000 -c 20 -P 16 2>&1 | grep -E "throughput summary|latency summary|avg.*min.*p50|^\s+[0-9]")
BM_PIPE_GET=$(redis-benchmark -h "$HOST" -p "$PORT" -t get -n 100000 -c 20 -P 16 2>&1 | grep -E "throughput summary|latency summary|avg.*min.*p50|^\s+[0-9]")
BM_PIPE_INCR=$(redis-benchmark -h "$HOST" -p "$PORT" -t incr -n 100000 -c 20 -P 16 2>&1 | grep -E "throughput summary|latency summary|avg.*min.*p50|^\s+[0-9]")
BM_PIPE_ZADD=$(redis-benchmark -h "$HOST" -p "$PORT" -t zadd -n 100000 -c 20 -P 16 2>&1 | grep -E "throughput summary|latency summary|avg.*min.*p50|^\s+[0-9]")

# ============================================================
# SECTION 4: Concurrency scaling (INCR — rate limit counter)
# ============================================================
echo "[4/7] Concurrency scaling for INCR..."
BM_C1=$(redis-benchmark -h "$HOST" -p "$PORT" -t incr -n 50000 -c 1 2>&1 | grep -E "throughput summary|latency summary|avg.*min.*p50|^\s+[0-9]")
BM_C10=$(redis-benchmark -h "$HOST" -p "$PORT" -t incr -n 50000 -c 10 2>&1 | grep -E "throughput summary|latency summary|avg.*min.*p50|^\s+[0-9]")
BM_C20=$(redis-benchmark -h "$HOST" -p "$PORT" -t incr -n 50000 -c 20 2>&1 | grep -E "throughput summary|latency summary|avg.*min.*p50|^\s+[0-9]")
BM_C50=$(redis-benchmark -h "$HOST" -p "$PORT" -t incr -n 50000 -c 50 2>&1 | grep -E "throughput summary|latency summary|avg.*min.*p50|^\s+[0-9]")
BM_C100=$(redis-benchmark -h "$HOST" -p "$PORT" -t incr -n 50000 -c 100 2>&1 | grep -E "throughput summary|latency summary|avg.*min.*p50|^\s+[0-9]")

# ============================================================
# SECTION 5: Realistic rate-limit Lua scripts
# ============================================================
echo "[5/7] Rate-limiter Lua EVAL benchmarks..."

# Fixed-window: INCR + EXPIRE (atomic)
BM_EVAL_FW=$(redis-benchmark -h "$HOST" -p "$PORT" -n 100000 -c 20 \
  eval "local k=KEYS[1]; local c=redis.call('INCR',k); if c==1 then redis.call('EXPIRE',k,5) end; return c" \
  1 "rl:bench:sec" 2>&1 | grep -E "throughput summary|latency summary|avg.*min.*p50|^\s+[0-9]")

# Sliding window: ZADD + ZREMRANGEBYSCORE + ZCARD + EXPIRE (atomic)
BM_EVAL_SW=$(redis-benchmark -h "$HOST" -p "$PORT" -n 100000 -c 20 \
  eval "local k=KEYS[1]; redis.call('ZADD',k,1000,1000); redis.call('ZREMRANGEBYSCORE',k,0,500); local cnt=redis.call('ZCARD',k); redis.call('EXPIRE',k,60); return cnt" \
  1 "rl:bench:sliding" 2>&1 | grep -E "throughput summary|latency summary|avg.*min.*p50|^\s+[0-9]")

# ============================================================
# SECTION 6: Payload size sensitivity
# ============================================================
echo "[6/7] Payload size sensitivity..."
BM_D16=$(redis-benchmark -h "$HOST" -p "$PORT" -t set,get -n 100000 -c 20 -d 16 2>&1 | grep -E "throughput summary|latency summary|avg.*min.*p50|^\s+[0-9]")
BM_D64=$(redis-benchmark -h "$HOST" -p "$PORT" -t set,get -n 100000 -c 20 -d 64 2>&1 | grep -E "throughput summary|latency summary|avg.*min.*p50|^\s+[0-9]")
BM_D512=$(redis-benchmark -h "$HOST" -p "$PORT" -t set,get -n 100000 -c 20 -d 512 2>&1 | grep -E "throughput summary|latency summary|avg.*min.*p50|^\s+[0-9]")

# ============================================================
# SECTION 7: Stress test
# ============================================================
echo "[7/7] Stress test (500k requests, 50 clients)..."
BM_STRESS=$(redis-benchmark -h "$HOST" -p "$PORT" -t incr,zadd -n 500000 -c 50 2>&1 | grep -E "throughput summary|latency summary|avg.*min.*p50|^\s+[0-9]")

echo ""
echo "All benchmarks complete. Writing results to $RESULTS_FILE ..."

# ============================================================
# Write raw results to file (append mode for re-runs)
# ============================================================
cat >> "$RESULTS_FILE" << RAWEOF

---
## Raw Benchmark Run — $TIMESTAMP
### Host: $HOST:$PORT  Redis: $REDIS_VERSION

#### Server Info
\`\`\`
$SERVER_INFO
\`\`\`

#### Memory Info
\`\`\`
$MEMORY_INFO
\`\`\`

#### Config
\`\`\`
maxmemory:        $CONFIG_MAXMEMORY
maxmemory-policy: $CONFIG_POLICY
tcp-backlog:      $CONFIG_BACKLOG
maxclients:       $CONFIG_MAXCLIENTS
hz:               $CONFIG_HZ
save:             $CONFIG_SAVE
appendonly:       $CONFIG_AOF
\`\`\`

#### SET (20c, no pipeline)
\`\`\`
$BM_BASIC_SET
\`\`\`
#### GET (20c, no pipeline)
\`\`\`
$BM_BASIC_GET
\`\`\`
#### INCR (20c, no pipeline)
\`\`\`
$BM_BASIC_INCR
\`\`\`
#### ZADD (20c, no pipeline)
\`\`\`
$BM_BASIC_ZADD
\`\`\`
#### SET pipelined P16 (20c)
\`\`\`
$BM_PIPE_SET
\`\`\`
#### GET pipelined P16 (20c)
\`\`\`
$BM_PIPE_GET
\`\`\`
#### INCR pipelined P16 (20c)
\`\`\`
$BM_PIPE_INCR
\`\`\`
#### ZADD pipelined P16 (20c)
\`\`\`
$BM_PIPE_ZADD
\`\`\`
#### INCR concurrency C1
\`\`\`
$BM_C1
\`\`\`
#### INCR concurrency C10
\`\`\`
$BM_C10
\`\`\`
#### INCR concurrency C20
\`\`\`
$BM_C20
\`\`\`
#### INCR concurrency C50
\`\`\`
$BM_C50
\`\`\`
#### INCR concurrency C100
\`\`\`
$BM_C100
\`\`\`
#### EVAL fixed-window INCR+EXPIRE (20c)
\`\`\`
$BM_EVAL_FW
\`\`\`
#### EVAL sliding-window ZADD+ZREM+ZCARD+EXPIRE (20c)
\`\`\`
$BM_EVAL_SW
\`\`\`
#### SET/GET 16-byte payload (20c)
\`\`\`
$BM_D16
\`\`\`
#### SET/GET 64-byte payload (20c)
\`\`\`
$BM_D64
\`\`\`
#### SET/GET 512-byte payload (20c)
\`\`\`
$BM_D512
\`\`\`
#### Stress INCR+ZADD 500k/50c
\`\`\`
$BM_STRESS
\`\`\`
RAWEOF

echo "Done. Results appended to: $RESULTS_FILE"
