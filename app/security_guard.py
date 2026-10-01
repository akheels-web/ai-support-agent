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


class RateLimitResult(dict):
    """Dict that evaluates to True if allowed, False if blocked/rate-limited."""
    def __bool__(self):
        return bool(self.get("allowed", False))


def check_rate_limit(key, limit=5, window_seconds=300, lock_seconds=None, max_attempts=None):
    """
    Returns RateLimitResult:
      allowed=True/False (also supports `if not check_rate_limit(...)`)
      reason
      retry_after
    """
    if max_attempts is not None:
        limit = max_attempts
    if lock_seconds is None:
        lock_seconds = window_seconds

    init_security_db()
    res = db.atomic_check_rate_limit(key, limit, window_seconds, lock_seconds)
    # Normalize return dict to match security_guard contract
    if res.get("allowed"):
        return RateLimitResult({
            "allowed": True,
            "reason": "allowed",
            "retry_after": 0,
        })
    else:
        return RateLimitResult({
            "allowed": False,
            "reason": res.get("reason", "rate_limited"),
            "retry_after": res.get("retry_after", lock_seconds),
        })


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
