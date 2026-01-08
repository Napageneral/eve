import json
import os
import socket
import sqlite3
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
EVE_BIN = REPO_ROOT / "bin" / "eve"


def pick_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def run_eve(*args: str, env: dict | None = None) -> dict:
    cmd = [str(EVE_BIN), *args]
    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
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


class TestEveRealFullPipeline(unittest.TestCase):
    def test_real_data_full_pipeline(self) -> None:
        # Real source DB (macOS Full Disk Access required).
        chat_db = (
            os.environ.get("EVE_SOURCE_CHAT_DB")
            or os.environ.get("CHATSTATS_SOURCE_CHAT_DB")
            or str(Path.home() / "Library" / "Messages" / "chat.db")
        )
        if not Path(chat_db).exists():
            raise AssertionError(f"Expected real chat.db at {chat_db}. Set EVE_SOURCE_CHAT_DB if different.")

        # Keep test isolated from user's real Eve app dir; still uses REAL source data.
        with tempfile.TemporaryDirectory(prefix="eve-real-full-pipeline-") as app_dir:
            redis_port = pick_free_port()
            context_port = pick_free_port()

            env = os.environ.copy()
            env["EVE_APP_DIR"] = app_dir
            env["EVE_SOURCE_CHAT_DB"] = chat_db
            env["EVE_LOG_TO_STDERR"] = "1"
            # Keep test logs quieter (stdout stays JSON-only regardless).
            env.setdefault("CELERY_LOG_LEVEL", "WARNING")

            # 1) init
            out = run_eve("init", "--app-dir", app_dir, "--source-chat-db", chat_db, env=env)
            self.assertTrue(out["ok"])
            db_path = str(out.get("db_path") or (Path(app_dir) / "eve.db"))

            # 2) sync real data (bounded window by default; override via env)
            since_days = float(os.environ.get("EVE_REAL_SYNC_SINCE_DAYS", "30"))
            out = run_eve(
                "sync",
                "--app-dir",
                app_dir,
                "--source-chat-db",
                chat_db,
                "--since-days",
                str(since_days),
                env=env,
            )
            self.assertTrue(out["ok"])

            # 3) status sanity (no message text)
            out = run_eve("status", "--app-dir", app_dir, env=env)
            self.assertTrue(out["ok"])
            counts = out.get("counts") or {}
            self.assertGreater(int(counts.get("messages") or 0), 0)
            self.assertGreater(int(counts.get("chats") or 0), 0)

            # Pick a small conversation to keep the test fast and avoid huge Celery payloads.
            convo_id = None
            chat_id = None
            con = sqlite3.connect(db_path)
            try:
                con.row_factory = sqlite3.Row
                row = con.execute(
                    """
                    SELECT c.id AS conversation_id, c.chat_id AS chat_id, COUNT(m.id) AS n
                    FROM conversations c
                    JOIN messages m ON m.conversation_id = c.id
                    GROUP BY c.id
                    ORDER BY n ASC, c.end_time DESC
                    LIMIT 1
                    """
                ).fetchone()
                if row:
                    convo_id = int(row["conversation_id"])
                    chat_id = int(row["chat_id"])
            finally:
                con.close()

            if convo_id is None or chat_id is None:
                raise AssertionError("No conversations found after sync")

            # 4) compute up (redis + context engine + celery)
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
                env=env,
            )
            self.assertTrue(out["ok"], f"compute up failed: {out}")

            try:
                # 5) trigger analysis on latest conversation and wait for embeddings
                out = run_eve(
                    "compute",
                    "analyze",
                    "--app-dir",
                    app_dir,
                    "--pass",
                    "basic",
                    "--conversation-id",
                    str(convo_id),
                    "--chat-id",
                    str(chat_id),
                    "--redis-port",
                    str(redis_port),
                    "--wait",
                    "--require-embeddings",
                    "--timeout-seconds",
                    str(float(os.environ.get("EVE_REAL_ANALYZE_TIMEOUT_S", "900"))),
                    env=env,
                )
                self.assertTrue(out["ok"], f"analysis pipeline failed: {out}")
                emb = (out.get("embeddings") or {})
                self.assertTrue(int(emb.get("count") or 0) > 0, f"expected embeddings > 0: {out}")
            finally:
                # Best-effort cleanup of compute plane processes
                try:
                    run_eve("compute", "down", "--app-dir", app_dir, env=env)
                except Exception:
                    pass


if __name__ == "__main__":
    unittest.main()


