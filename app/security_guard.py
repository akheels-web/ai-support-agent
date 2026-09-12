import os
import time
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "dashboard.db"))


def _db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_security_db():
    conn = _db()

    conn.execute("""
    CREATE TABLE IF NOT EXISTS security_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT,
        key TEXT,
        details TEXT,
        created_at INTEGER
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS rate_limits (
        key TEXT PRIMARY KEY,
        counter INTEGER DEFAULT 0,
        window_start INTEGER,
        locked_until INTEGER DEFAULT 0
    )
    """)

    conn.commit()
    conn.close()


def log_security_event(event_type, key="", details=""):
    init_security_db()

    conn = _db()
    conn.execute(
        """
        INSERT INTO security_events(event_type, key, details, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (event_type, key, details, int(time.time()))
    )
    conn.commit()
    conn.close()


def check_rate_limit(key, limit, window_seconds, lock_seconds):
    """
    Returns:
      allowed=True/False
      reason
      retry_after
    """

    init_security_db()

    now = int(time.time())
    conn = _db()

    row = conn.execute(
        "SELECT * FROM rate_limits WHERE key=?",
        (key,)
    ).fetchone()

    if row:
        locked_until = int(row["locked_until"] or 0)

        if locked_until > now:
            conn.close()
            return {
                "allowed": False,
                "reason": "locked",
                "retry_after": locked_until - now
            }

        window_start = int(row["window_start"] or now)
        counter = int(row["counter"] or 0)

        if now - window_start > window_seconds:
            counter = 1
            window_start = now
            locked_until = 0
        else:
            counter += 1

        if counter > limit:
            locked_until = now + lock_seconds
            conn.execute(
                """
                UPDATE rate_limits
                SET counter=?, window_start=?, locked_until=?
                WHERE key=?
                """,
                (counter, window_start, locked_until, key)
            )
            conn.commit()
            conn.close()

            return {
                "allowed": False,
                "reason": "rate_limited",
                "retry_after": lock_seconds
            }

        conn.execute(
            """
            UPDATE rate_limits
            SET counter=?, window_start=?, locked_until=0
            WHERE key=?
            """,
            (counter, window_start, key)
        )
        conn.commit()
        conn.close()

        return {
            "allowed": True,
            "reason": "allowed",
            "retry_after": 0
        }

    conn.execute(
        """
        INSERT INTO rate_limits(key, counter, window_start, locked_until)
        VALUES (?, 1, ?, 0)
        """,
        (key, now)
    )
    conn.commit()
    conn.close()

    return {
        "allowed": True,
        "reason": "allowed",
        "retry_after": 0
    }


def is_locked(key):
    """Read-only lock check. Returns remaining lock seconds, or 0 if not locked.
    Does NOT increment the counter (unlike check_rate_limit)."""
    init_security_db()

    now = int(time.time())
    conn = _db()
    row = conn.execute("SELECT locked_until FROM rate_limits WHERE key=?", (key,)).fetchone()
    conn.close()

    if row and int(row["locked_until"] or 0) > now:
        return int(row["locked_until"]) - now
    return 0


def reset_rate_limit(key):
    init_security_db()

    conn = _db()
    conn.execute("DELETE FROM rate_limits WHERE key=?", (key,))
    conn.commit()
    conn.close()
