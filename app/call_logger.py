import os
import re
import time
from datetime import datetime
from pathlib import Path
import app.db as db

BASE_DIR = Path(__file__).resolve().parent.parent
RECORDING_DIR = os.getenv("RECORDING_DIR", "/var/spool/asterisk/monitor/ai-support")


_CALL_DB_INITIALIZED = False


def init_call_db():
    global _CALL_DB_INITIALIZED
    if not _CALL_DB_INITIALIZED:
        db.init_all_tables()
        _CALL_DB_INITIALIZED = True


def create_call(call_id, status="in_progress"):
    init_call_db()
    now = int(time.time())

    conn = db.get_db()
    try:
        conn.execute(
            """
            INSERT INTO calls(call_id, start_time, status)
            VALUES (?, ?, ?)
            ON CONFLICT (call_id) DO NOTHING
            """,
            (call_id, now, status)
        )
        conn.commit()
    finally:
        conn.close()


def rename_call_id(old_call_id, new_call_id):
    if not old_call_id or not new_call_id or old_call_id == new_call_id:
        return

    init_call_db()
    conn = db.get_db()
    try:
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
    finally:
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
        "transcript",
        "is_vip",
        "transferred",
        "transfer_target",
        "tier",
        "escalation_reason",
        "resolution_type",
        "ai_deflected",
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

    conn = db.get_db()
    try:
        conn.execute(
            f"UPDATE calls SET {', '.join(fields)} WHERE call_id=?",
            values
        )
        conn.commit()
    finally:
        conn.close()


def close_call(call_id, status="completed", summary=None, transcript=None):
    init_call_db()

    recording_file = find_recording_by_call_id(call_id)
    caller_number = extract_caller_from_recording(recording_file) if recording_file else None

    conn = db.get_db()
    try:
        row = conn.execute(
            "SELECT start_time, verified_name, employee_id, caller_number FROM calls WHERE call_id=?",
            (call_id,)
        ).fetchone()

        end_time = int(time.time())
        duration_seconds = None

        if row and row.get("start_time"):
            duration_seconds = end_time - int(row["start_time"])

        # Format and save recording by name: username_time_day_year.wav
        if recording_file and Path(recording_file).exists():
            try:
                rec_path = Path(recording_file)
                raw_user = None
                if row:
                    raw_user = row.get("verified_name") or row.get("employee_id") or row.get("caller_number")
                if not raw_user:
                    raw_user = caller_number or "caller"

                clean_user = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', str(raw_user)).strip('_')
                if not clean_user:
                    clean_user = "caller"

                st = int(row["start_time"]) if (row and row.get("start_time")) else end_time
                dt = datetime.fromtimestamp(st)
                time_str = dt.strftime("%H-%M-%S")
                day_str = dt.strftime("%A")
                year_str = dt.strftime("%Y")
                new_filename = f"{clean_user}_{time_str}_{day_str}_{year_str}.wav"
                new_path = rec_path.parent / new_filename

                if new_path != rec_path:
                    time.sleep(0.2)
                    rec_path.rename(new_path)
                    recording_file = str(new_path)
            except Exception as ren_err:
                print(f"[RECORDING RENAME] Notice: {ren_err}")

        conn.execute(
            """
            UPDATE calls
            SET end_time=?,
                duration_seconds=?,
                status=?,
                recording_file=COALESCE(?, recording_file),
                caller_number=COALESCE(?, caller_number),
                summary=COALESCE(?, summary),
                transcript=COALESCE(?, transcript)
            WHERE call_id=?
            """,
            (
                end_time,
                duration_seconds,
                status,
                recording_file,
                caller_number,
                summary,
                transcript,
                call_id,
            )
        )
        conn.commit()
    finally:
        conn.close()


def reconcile_stale_calls(max_age_seconds=1800):
    init_call_db()

    now = int(time.time())
    cutoff = now - max_age_seconds

    conn = db.get_db()
    try:
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
    finally:
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