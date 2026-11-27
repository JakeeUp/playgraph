"""Run a bounded public-read smoke test against disposable local data."""
import argparse
import csv
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
ROUTES = {"/app", "/games", "/games?q=", "/games/{id}", "/games/{id}/reviews",
          "/feed", "/reviews/{id}/comments"}


def app_environment(work, host, database_url, redis_url):
    return {**os.environ, "PYTHONPATH": str(ROOT), "DATABASE_URL": database_url,
            "REDIS_URL": redis_url, "APP_BASE_URL": host, "ENVIRONMENT": "development",
            "JWT_SECRET": "synthetic-ci-signing-key-not-for-real-accounts", "STEAM_API_KEY": "synthetic-ci-key",
            "DEMO_MODE": "false", "CLERK_ENABLED": "false", "CLERK_SECRET_KEY": "", "CLERK_PUBLISHABLE_KEY": "",
            "PSN_NPSSO": "", "IGDB_CLIENT_ID": "", "IGDB_CLIENT_SECRET": "", "APITUBE_API_KEY": "",
            "IGDB_CATALOG_SYNC_ENABLED": "false", "AWS_BACKUP_BUCKET": "", "BETA_INVITE_ONLY": "false",
            "PLAYGRAPH_BACKUP_DIR": str(work / "backups")}


def stop_server(server):
    if server.poll() is not None:
        return
    # Windows virtualenv Python launches a child which also holds SQLite open.
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(server.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait()


def check_stats(path, threshold):
    if not math.isfinite(threshold) or threshold <= 0:
        raise ValueError("p95 limit must be a positive finite number")
    with Path(path).open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if any(int(row["Failure Count"]) for row in rows):
        raise ValueError("Locust recorded failed requests")
    timings = {}
    for row in rows:
        if row["Type"] == "GET" and row["Name"] in ROUTES:
            timing = float(row["95%"])
            if int(row["Request Count"]) < 5 or not math.isfinite(timing) or timing < 0:
                raise ValueError(f"Insufficient valid samples for {row['Name']}")
            timings[row["Name"]] = timing
    if timings.keys() != ROUTES:
        raise ValueError("Missing public-route measurements")
    worst = max(timings.values())
    if worst > threshold:
        raise ValueError(f"Worst route p95 {worst:g} ms exceeds {threshold:g} ms")
    return {"p95_ms": timings, "worst_p95_ms": worst, "threshold_ms": threshold}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--p95-ms", type=float, default=1410)
    parser.add_argument("--redis-url", default="redis://127.0.0.1:6379/0")
    parser.add_argument("--output", type=Path, default=ROOT / "test-results" / "performance")
    args = parser.parse_args()
    redis = urlsplit(args.redis_url)
    if redis.scheme != "redis" or redis.hostname not in {"127.0.0.1", "localhost"}:
        parser.error("Use an isolated loopback Redis, never a hosted service")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="playgraph-perf-") as directory:
        work = Path(directory)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        host = f"http://127.0.0.1:{port}"
        env = app_environment(work, host, "sqlite:///" + (work / "synthetic.db").as_posix(), args.redis_url)
        with (output / "server.log").open("w", encoding="utf-8") as log:
            for module, extra in (("app.migrations", ["upgrade"]), ("scripts.seed_demo", [])):
                subprocess.run([sys.executable, "-m", module, *extra], cwd=work, env=env,
                               stdout=log, stderr=subprocess.STDOUT, check=True, timeout=30)
            server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                       "--port", str(port), "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"],
                                      cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                ready = False
                for _ in range(100):
                    if server.poll() is not None:
                        raise RuntimeError("Local API exited; see server.log")
                    try:
                        with urlopen(host + "/health", timeout=1) as response:
                            ready = response.status == 200
                    except URLError:
                        pass
                    if ready:
                        break
                    time.sleep(0.1)
                if not ready:
                    raise RuntimeError("Local API did not become ready")
                with (output / "locust.log").open("w", encoding="utf-8") as load_log:
                    subprocess.run([sys.executable, "-m", "locust", "-f", str(ROOT / "loadtest" / "locustfile.py"),
                                    "--host", host, "--headless", "-u", "50", "-r", "50", "-t", "35s",
                                    "--csv", str(output / "locust"), "--exit-code-on-error", "1"],
                                   cwd=work, env=env, stdout=load_log, stderr=subprocess.STDOUT, check=True, timeout=55)
            finally:
                stop_server(server)
    report = check_stats(output / "locust_stats.csv", args.p95_ms)
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
