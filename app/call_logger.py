import time
import sqlite3
from pathlib import Path

DB_PATH = "/opt/ai-support-agent/data/dashboard.db"
RECORDING_DIR = "/var/spool/asterisk/monitor/ai-support"


def _db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _ensure_column(conn, table, column, definition):
    columns = conn.execute(f"PRAGMA table_info({table})").fetchall()
    existing = {row["name"] for row in columns}

    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_call_db():
    Path("/opt/ai-support-agent/data").mkdir(parents=True, exist_ok=True)

    conn = _db()

    conn.execute("""
    CREATE TABLE IF NOT EXISTS calls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        call_id TEXT,
        caller_number TEXT,
        called_number TEXT,
        employee_id TEXT,
        verified_name TEXT,
        language TEXT,
        start_time INTEGER,
        end_time INTEGER,
        duration_seconds INTEGER,
        status TEXT,
        ticket_number TEXT,
        ticket_created INTEGER DEFAULT 0,
        recording_file TEXT,
        summary TEXT
    )
    """)

    _ensure_column(conn, "calls", "is_vip", "INTEGER DEFAULT 0")
    _ensure_column(conn, "calls", "transferred", "INTEGER DEFAULT 0")
    _ensure_column(conn, "calls", "transfer_target", "TEXT")

    conn.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_calls_call_id
    ON calls(call_id)
    """)

    conn.commit()
    conn.close()


def create_call(call_id, status="in_progress"):
    init_call_db()

    conn = _db()
    now = int(time.time())

    conn.execute(
        """
        INSERT OR IGNORE INTO calls(call_id, start_time, status)
        VALUES (?, ?, ?)
        """,
        (call_id, now, status)
    )

    conn.commit()
    conn.close()


def rename_call_id(old_call_id, new_call_id):
    if not old_call_id or not new_call_id or old_call_id == new_call_id:
        return

    init_call_db()

    conn = _db()

    old_row = conn.execute(
        "SELECT id FROM calls WHERE call_id=?",
        (old_call_id,)
    ).fetchone()

    new_row = conn.execute(
        "SELECT id FROM calls WHERE call_id=?",
        (new_call_id,)
    ).fetchone()

    if old_row and not new_row:
        conn.execute(
            "UPDATE calls SET call_id=? WHERE call_id=?",
            (new_call_id, old_call_id)
        )

    elif old_row and new_row:
        conn.execute(
            "DELETE FROM calls WHERE call_id=?",
            (old_call_id,)
        )

    conn.commit()
    conn.close()


def update_call(call_id, **kwargs):
    if not kwargs:
        return

    init_call_db()

    allowed = {
        "caller_number",
        "called_number",
        "employee_id",
        "verified_name",
        "language",
        "status",
        "ticket_number",
        "ticket_created",
        "recording_file",
        "summary",
        "is_vip",
        "transferred",
        "transfer_target",
    }

    fields = []
    values = []

    for key, value in kwargs.items():
        if key in allowed:
            fields.append(f"{key}=?")
            values.append(value)

    if not fields:
        return

    values.append(call_id)

    conn = _db()
    conn.execute(
        f"UPDATE calls SET {', '.join(fields)} WHERE call_id=?",
        values
    )
    conn.commit()
    conn.close()


def close_call(call_id, status="completed"):
    init_call_db()

    recording_file = find_recording_by_call_id(call_id)
    caller_number = extract_caller_from_recording(recording_file) if recording_file else None

    conn = _db()

    row = conn.execute(
        "SELECT start_time FROM calls WHERE call_id=?",
        (call_id,)
    ).fetchone()

    end_time = int(time.time())
    duration_seconds = None

    if row and row["start_time"]:
        duration_seconds = end_time - int(row["start_time"])

    conn.execute(
        """
        UPDATE calls
        SET end_time=?,
            duration_seconds=?,
            status=?,
            recording_file=COALESCE(?, recording_file),
            caller_number=COALESCE(?, caller_number)
        WHERE call_id=?
        """,
        (
            end_time,
            duration_seconds,
            status,
            recording_file,
            caller_number,
            call_id,
        )
    )

    conn.commit()
    conn.close()


def reconcile_stale_calls(max_age_seconds=1800):
    init_call_db()

    now = int(time.time())
    cutoff = now - max_age_seconds

    conn = _db()
    conn.execute(
        """
        UPDATE calls
        SET status='ended',
            end_time=COALESCE(end_time, ?),
            duration_seconds=CASE
                WHEN start_time IS NOT NULL THEN ? - start_time
                ELSE duration_seconds
            END
        WHERE end_time IS NULL
        AND start_time < ?
        AND status IN ('in_progress', 'language_selected', 'verified', 'troubleshooting')
        """,
        (now, now, cutoff)
    )
    conn.commit()
    conn.close()


def find_recording_by_call_id(call_id):
    base = Path(RECORDING_DIR)

    if not base.exists():
        return None

    matches = list(base.glob(f"*{call_id}*.wav"))

    if not matches:
        return None

    latest = sorted(matches, key=lambda p: p.stat().st_mtime, reverse=True)[0]
    return str(latest)


def extract_caller_from_recording(recording_file):
    if not recording_file:
        return None

    name = Path(recording_file).name
    parts = name.split("-")

    if len(parts) >= 4:
        return parts[2]

    return None