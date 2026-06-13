import time
import sqlite3
from pathlib import Path

DB_PATH = "/opt/ai-support-agent/data/dashboarddb():DB_PATH = "/opt/ai-support-agent/data/dashboard.db"
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
    conn.commit()
    conn.close()


def create_call(call_id, status="in_progress"):
    init_call_db()

    conn = _db()

    existing = conn.execute(
        "SELECT id FROM calls WHERE call_id=?",
        (call_id,)
    ).fetchone()

    if not existing:
        conn.execute(
            """
            INSERT INTO calls(call_id, start_time, status)
            VALUES (?, ?, ?)
            """,
            (call_id, int(time.time()), status)
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

    # Example:
    # 20260612-185618-+9680097165540006-1781290578.122.wav
    parts = name.split("-")

    if len(parts) >= 4:
        return parts[2]

    return None
RECORDING_DIR = "/var/spool/asterisk/monitor/ai-support"