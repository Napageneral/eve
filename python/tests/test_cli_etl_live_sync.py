import json
import os
import sqlite3
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
EVE_BIN = REPO_ROOT / "bin" / "eve"


def apple_epoch_ns(dt: datetime) -> int:
    base = datetime(2001, 1, 1, tzinfo=timezone.utc)
    return int((dt - base).total_seconds() * 1_000_000_000)


def create_fixture_chat_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS handle (
              ROWID INTEGER PRIMARY KEY,
              id TEXT
            );

            CREATE TABLE IF NOT EXISTS chat (
              ROWID INTEGER PRIMARY KEY,
              guid TEXT,
              chat_identifier TEXT,
              display_name TEXT,
              service_name TEXT
            );

            CREATE TABLE IF NOT EXISTS chat_handle_join (
              chat_id INTEGER,
              handle_id INTEGER
            );

            CREATE TABLE IF NOT EXISTS message (
              ROWID INTEGER PRIMARY KEY,
              guid TEXT,
              text TEXT,
              attributedBody BLOB,
              handle_id INTEGER,
              service TEXT,
              date INTEGER,
              is_from_me INTEGER,
              associated_message_guid TEXT,
              associated_message_type INTEGER,
              reply_to_guid TEXT
            );

            CREATE TABLE IF NOT EXISTS chat_message_join (
              chat_id INTEGER,
              message_id INTEGER
            );

            CREATE TABLE IF NOT EXISTS attachment (
              ROWID INTEGER PRIMARY KEY,
              guid TEXT,
              created_date INTEGER,
              filename TEXT,
              uti TEXT,
              mime_type TEXT,
              total_bytes INTEGER,
              is_sticker INTEGER
            );

            CREATE TABLE IF NOT EXISTS message_attachment_join (
              message_id INTEGER,
              attachment_id INTEGER
            );
            """
        )

        # Seed handles
        conn.execute("INSERT INTO handle (ROWID, id) VALUES (?, ?)", (1, "+15555550100"))
        conn.execute("INSERT INTO handle (ROWID, id) VALUES (?, ?)", (2, "+15555550200"))

        # Seed one chat with two participants
        conn.execute(
            "INSERT INTO chat (ROWID, guid, chat_identifier, display_name, service_name) VALUES (?, ?, ?, ?, ?)",
            (1, "chat-guid-1", "chat-1", "Test Chat", "iMessage"),
        )
        conn.execute("INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (?, ?)", (1, 1))
        conn.execute("INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (?, ?)", (1, 2))

        # Seed two messages (one inbound, one outbound)
        t0 = datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
        t1 = datetime(2026, 1, 1, 0, 0, 5, tzinfo=timezone.utc)

        conn.execute(
            "INSERT INTO message (ROWID, guid, text, attributedBody, handle_id, service, date, is_from_me, associated_message_guid, associated_message_type, reply_to_guid) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, "m1", "hello", None, 1, "iMessage", apple_epoch_ns(t0), 0, None, None, None),
        )
        conn.execute(
            "INSERT INTO message (ROWID, guid, text, attributedBody, handle_id, service, date, is_from_me, associated_message_guid, associated_message_type, reply_to_guid) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (2, "m2", "hi", None, None, "iMessage", apple_epoch_ns(t1), 1, None, None, None),
        )
        conn.execute("INSERT INTO chat_message_join (chat_id, message_id) VALUES (?, ?)", (1, 1))
        conn.execute("INSERT INTO chat_message_join (chat_id, message_id) VALUES (?, ?)", (1, 2))

        # Seed one attachment on message 1
        conn.execute(
            "INSERT INTO attachment (ROWID, guid, created_date, filename, uti, mime_type, total_bytes, is_sticker) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (1, "a1", apple_epoch_ns(t0), "pic.jpg", "public.jpeg", "image/jpeg", 1234, 0),
        )
        conn.execute("INSERT INTO message_attachment_join (message_id, attachment_id) VALUES (?, ?)", (1, 1))

        conn.commit()
    finally:
        conn.close()


def insert_new_message(chat_db: Path, rowid: int, guid: str, text: str) -> None:
    conn = sqlite3.connect(str(chat_db))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        t = datetime(2026, 1, 1, 0, 1, 0, tzinfo=timezone.utc)
        conn.execute(
            "INSERT INTO message (ROWID, guid, text, attributedBody, handle_id, service, date, is_from_me, associated_message_guid, associated_message_type, reply_to_guid) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rowid, guid, text, None, 1, "iMessage", apple_epoch_ns(t), 0, None, None, None),
        )
        conn.execute("INSERT INTO chat_message_join (chat_id, message_id) VALUES (?, ?)", (1, rowid))
        conn.commit()
    finally:
        conn.close()


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


class TestEveCliEtlLiveSync(unittest.TestCase):
    def test_init_sync_watch(self):
        with tempfile.TemporaryDirectory(prefix="eve-test-appdir-") as app_dir:
            app_dir_p = Path(app_dir)
            chat_db = app_dir_p / "chat.db"
            create_fixture_chat_db(chat_db)

            # init
            out = run_eve("init", "--app-dir", app_dir, "--source-chat-db", str(chat_db))
            self.assertTrue(out["ok"])
            db_path = Path(out["db_path"])
            self.assertTrue(db_path.exists(), f"Expected eve.db to exist at {db_path}")

            # sync (skip contacts to keep this hermetic)
            out = run_eve("sync", "--app-dir", app_dir, "--source-chat-db", str(chat_db), "--no-contacts")
            self.assertTrue(out["ok"])

            # status should show imported rows
            out = run_eve("status", "--app-dir", app_dir)
            self.assertTrue(out["ok"])
            counts = out["counts"]
            self.assertGreaterEqual(counts["messages"], 2)
            self.assertGreaterEqual(counts["chats"], 1)

            # Start watch in background (bounded) and inject a new message
            env = os.environ.copy()
            env["EVE_APP_DIR"] = app_dir
            env["EVE_SOURCE_CHAT_DB"] = str(chat_db)
            env["EVE_LOG_TO_STDERR"] = "1"

            watch_proc = subprocess.Popen(
                [str(EVE_BIN), "watch", "--app-dir", app_dir, "--source-chat-db", str(chat_db), "--seconds", "2", "--max-batches", "5", "--no-contacts", "--no-conversation-tracking"],
                cwd=str(REPO_ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            try:
                time.sleep(0.3)
                insert_new_message(chat_db, rowid=3, guid="m3", text="new message")

                stdout, stderr = watch_proc.communicate(timeout=10)
                self.assertEqual(watch_proc.returncode, 0, f"watch failed. stdout:\n{stdout}\nstderr:\n{stderr}")
                watch_out = json.loads(stdout)
                self.assertTrue(watch_out["ok"])
            finally:
                if watch_proc.poll() is None:
                    watch_proc.kill()

            # Verify DB has the new message
            out = run_eve("status", "--app-dir", app_dir)
            self.assertGreaterEqual(out["counts"]["messages"], 3)


if __name__ == "__main__":
    unittest.main()


