"""
run_benchmark.py — Orchestrator script for the Locust load test.

This script:
1. Starts Redis via WSL as a LONG-LIVED subprocess (kept alive via Popen)
2. Starts the FastAPI test app (uvicorn) in a subprocess
3. Waits for the app to be ready
4. Runs Locust headless for 20 seconds
5. Kills uvicorn and Redis
6. Reports final Redis key state

Run from repo root:
    python tests/run_benchmark.py
"""

import json
import subprocess
import sys
import time
import urllib.request
import os
import threading

REDIS_URL = "redis://localhost:6379/15"
APP_PORT = 8099
APP_URL = f"http://localhost:{APP_PORT}"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def start_redis_persistent():
    """
    Start Redis via WSL as a long-lived subprocess.
    Returns (redis_proc, redis_client).
    The subprocess stays alive for the life of this script.
    """
    import redis as redis_lib

    # Kill any existing Redis
    subprocess.run(
        ["wsl", "bash", "-c", "pkill redis-server 2>/dev/null; sleep 0.3"],
        capture_output=True,
    )

    # Start Redis as a non-daemonized foreground process — this subprocess
    # will stay alive as long as our script holds the Popen reference.
    proc = subprocess.Popen(
        ["wsl", "redis-server",
         "--bind", "0.0.0.0",
         "--protected-mode", "no",
         "--port", "6379",
         "--loglevel", "warning"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Drain stdout/stderr in background threads to prevent pipe deadlock
    def drain(pipe):
        for _ in pipe:
            pass

    threading.Thread(target=drain, args=(proc.stdout,), daemon=True).start()
    threading.Thread(target=drain, args=(proc.stderr,), daemon=True).start()

    # Wait for Redis to be ready
    deadline = time.time() + 10
    while time.time() < deadline:
        time.sleep(0.5)
        try:
            r = redis_lib.from_url(REDIS_URL, socket_connect_timeout=1.0, socket_timeout=2.0)
            r.ping()
            print(f"[setup] Redis started (PID={proc.pid})")
            r.flushdb()
            print("[setup] DB 15 flushed")
            return proc, r
        except Exception:
            pass

    proc.kill()
    raise RuntimeError("Redis did not start within 10 seconds")


def start_app(redis_proc_pid):
    """Start uvicorn as a subprocess and return the Popen object."""
    venv_dir = os.path.join(REPO_ROOT, "venv", "Scripts")
    uvicorn_exe = os.path.join(venv_dir, "uvicorn.exe")
    python_exe = os.path.join(venv_dir, "python.exe")

    if os.path.exists(uvicorn_exe):
        cmd = [uvicorn_exe, "tests.app_for_locust:app",
               "--port", str(APP_PORT), "--log-level", "warning"]
    elif os.path.exists(python_exe):
        cmd = [python_exe, "-m", "uvicorn", "tests.app_for_locust:app",
               "--port", str(APP_PORT), "--log-level", "warning"]
    else:
        cmd = [sys.executable, "-m", "uvicorn", "tests.app_for_locust:app",
               "--port", str(APP_PORT), "--log-level", "warning"]

    print(f"[setup] Starting uvicorn: {os.path.basename(cmd[0])}")
    proc = subprocess.Popen(
        cmd,
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "REDIS_URL": REDIS_URL},
    )

    def drain(pipe):
        for _ in pipe:
            pass

    threading.Thread(target=drain, args=(proc.stdout,), daemon=True).start()
    threading.Thread(target=drain, args=(proc.stderr,), daemon=True).start()

    return proc


def wait_for_app(timeout=15):
    """Poll until app responds or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = urllib.request.urlopen(f"{APP_URL}/ping?user_id=startup", timeout=2)
            body = json.loads(resp.read())
            print(f"[setup] App ready: {body}")
            return True
        except Exception as e:
            print(f"[setup] Waiting for app... ({type(e).__name__})")
            time.sleep(1)
    return False


def run_locust():
    """Run locust headless and capture output."""
    venv_dir = os.path.join(REPO_ROOT, "venv", "Scripts")
    locust_exe = os.path.join(venv_dir, "locust.exe")
    if not os.path.exists(locust_exe):
        locust_exe = "locust"

    locustfile = os.path.join(REPO_ROOT, "tests", "locustfile.py")
    cmd = [
        locust_exe,
        "-f", locustfile,
        "--headless",
        "-u", "15",
        "-r", "3",
        "--run-time", "20s",
        "--host", APP_URL,
        "--csv", os.path.join(REPO_ROOT, "tests", "locust_results"),
        "--html", os.path.join(REPO_ROOT, "tests", "locust_report.html"),
    ]
    print(f"\n[locust] Starting headless run (15 users, 20s)...")
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)
    return result


def report_redis_keys(r):
    """Print all Redis keys in DB 15 after the benchmark."""
    try:
        keys = sorted(r.keys("*"))
        print("\n[redis] Keys in DB 15 after benchmark:")
        if not keys:
            print("  (no keys — DB may have been flushed)")
            return
        for k in keys:
            key_type = r.type(k)
            if key_type == "zset":
                size = r.zcard(k)
                # Show score range too
                members = r.zrangebyscore(k, "-inf", "+inf", withscores=True)
                print(f"  {k}")
                print(f"    type=zset  ZCARD={size}")
            elif key_type == "string":
                val = r.get(k)
                ttl = r.ttl(k)
                print(f"  {k}")
                print(f"    type=str   value={val}  TTL={ttl}s")
            else:
                print(f"  {k}  [{key_type}]")
    except Exception as e:
        print(f"[redis] Error reading keys: {e}")


def read_csv_stats():
    """Read and print Locust CSV stats if available."""
    csv_path = os.path.join(REPO_ROOT, "tests", "locust_results_stats.csv")
    if not os.path.exists(csv_path):
        print("[locust] CSV stats not found")
        return
    with open(csv_path, encoding="utf-8") as f:
        lines = f.readlines()
    print("\n[locust] Stats CSV (raw):")
    for line in lines:
        stripped = line.strip()
        if stripped and stripped != "Type,Name,Request Count,Failure Count,Median Response Time,Average Response Time,Min Response Time,Max Response Time,Average Content Size,Requests/s,Failures/s,50%,66%,75%,80%,90%,95%,98%,99%,99.9%,99.99%,100%":
            print("  " + stripped)


def main():
    redis_proc = None
    app_proc = None

    try:
        # Step 1: Redis (kept alive via subprocess)
        redis_proc, r = start_redis_persistent()

        # Step 2: Start app
        app_proc = start_app(redis_proc.pid)

        # Step 3: Wait for app
        if not wait_for_app(timeout=15):
            print("[error] App did not start in 15s")
            return

        # Step 4: Run Locust
        locust_result = run_locust()

        print("\n[locust] STDERR output (stats table):")
        # Extract the stats table from stderr
        stderr_lines = locust_result.stderr.splitlines()
        in_table = False
        for line in stderr_lines:
            if "Name" in line and "# reqs" in line:
                in_table = True
            if in_table:
                print("  " + line)
            if in_table and line.strip().startswith("----") and "Aggregated" in "\n".join(stderr_lines):
                pass  # keep printing

        # Print the custom summary from stdout
        if locust_result.stdout.strip():
            print("\n[locust] Custom summary:")
            print(locust_result.stdout)

        print(f"\n[locust] Return code: {locust_result.returncode}")

        # Step 5: CSV stats
        read_csv_stats()

        # Step 6: Redis state
        report_redis_keys(r)

    finally:
        # Step 7: Kill processes
        if app_proc:
            app_proc.kill()
            try:
                app_proc.wait(timeout=3)
            except Exception:
                pass
            print("\n[setup] uvicorn stopped")

        if redis_proc:
            redis_proc.kill()
            try:
                redis_proc.wait(timeout=3)
            except Exception:
                pass
            print("[setup] Redis stopped")


if __name__ == "__main__":
    main()
