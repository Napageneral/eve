import json
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
EVE_BIN = REPO_ROOT / "bin" / "eve"


def _has_celery() -> bool:
    try:
        import celery  # noqa: F401
        return True
    except Exception:
        return False


HAS_COMPUTE_DEPS = bool(shutil.which("redis-server") and shutil.which("redis-cli") and shutil.which("bun") and _has_celery())


def pick_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def run_eve(*args: str) -> dict:
    cmd = [str(EVE_BIN), *args]
    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.stdout.strip() == "":
        raise AssertionError(f"eve produced no stdout. stderr:\n{proc.stderr}")
    try:
        data = json.loads(proc.stdout)
    except Exception as e:
        raise AssertionError(f"Failed to parse JSON from stdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}") from e
    if proc.returncode != 0:
        raise AssertionError(f"eve exited {proc.returncode}: {data}\nstderr:\n{proc.stderr}")
    return data


@unittest.skipUnless(HAS_COMPUTE_DEPS, "compute deps missing (bun/redis/celery)")
class TestEveCliComputeOrchestration(unittest.TestCase):
    def test_compute_up_status_down(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eve-test-compute-") as app_dir:
            redis_port = pick_free_port()
            context_port = pick_free_port()

            out = run_eve("init", "--app-dir", app_dir)
            self.assertTrue(out["ok"])

            try:
                out = run_eve(
                    "compute",
                    "up",
                    "--app-dir",
                    app_dir,
                    "--redis-port",
                    str(redis_port),
                    "--context-port",
                    str(context_port),
                    "--celery-concurrency",
                    "2",
                )
                self.assertTrue(out["ok"])

                deadline = time.time() + 20.0
                last = None
                while time.time() < deadline:
                    last = run_eve(
                        "compute",
                        "status",
                        "--app-dir",
                        app_dir,
                        "--redis-port",
                        str(redis_port),
                        "--context-port",
                        str(context_port),
                    )
                    s = last.get("status", {})
                    if (
                        s.get("redis_ping", {}).get("ok")
                        and s.get("context_engine_health", {}).get("ok")
                        and s.get("celery_ping", {}).get("ok")
                    ):
                        break
                    time.sleep(0.5)

                self.assertIsNotNone(last)
                s = last["status"]
                self.assertTrue(s["redis_ping"]["ok"], f"redis ping failed: {s.get('redis_ping')}")
                self.assertTrue(s["context_engine_health"]["ok"], f"context engine health failed: {s.get('context_engine_health')}")
                self.assertTrue(s["celery_ping"]["ok"], f"celery ping failed: {s.get('celery_ping')}")
            finally:
                # Best-effort cleanup (even if up/status failed)
                try:
                    run_eve("compute", "down", "--app-dir", app_dir)
                except Exception:
                    pass


