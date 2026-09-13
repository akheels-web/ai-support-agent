import time
import app.db as db


_SECURITY_DB_INITIALIZED = False


def init_security_db():
    global _SECURITY_DB_INITIALIZED
    if not _SECURITY_DB_INITIALIZED:
        db.init_all_tables()
        _SECURITY_DB_INITIALIZED = True


def log_security_event(event_type, key="", details=""):
    init_security_db()

    conn = db.get_db()
    try:
        conn.execute(
            """
            INSERT INTO security_events(event_type, key, details, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (event_type, key, details, int(time.time()))
        )
        conn.commit()
    finally:
        conn.close()


def check_rate_limit(key, limit, window_seconds, lock_seconds):
    """
    Returns:
      allowed=True/False
      reason
      retry_after
    """
    init_security_db()
    res = db.atomic_check_rate_limit(key, limit, window_seconds, lock_seconds)
    # Normalize return dict to match security_guard contract
    if res.get("allowed"):
        return {
            "allowed": True,
            "reason": "allowed",
            "retry_after": 0,
        }
    else:
        return {
            "allowed": False,
            "reason": res.get("reason", "rate_limited"),
            "retry_after": res.get("retry_after", lock_seconds),
        }


def is_locked(key):
    """Read-only lock check. Returns remaining lock seconds, or 0 if not locked.
    Does NOT increment the counter (unlike check_rate_limit)."""
    init_security_db()

    now = int(time.time())
    conn = db.get_db()
    try:
        row = conn.execute("SELECT locked_until FROM rate_limits WHERE key=?", (key,)).fetchone()
        if row and int(row.get("locked_until") or 0) > now:
            return int(row["locked_until"]) - now
        return 0
    finally:
        conn.close()


def reset_rate_limit(key):
    init_security_db()

    conn = db.get_db()
    try:
        conn.execute("DELETE FROM rate_limits WHERE key=?", (key,))
        conn.commit()
    finally:
        conn.close()
