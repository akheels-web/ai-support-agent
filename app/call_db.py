import sqlite3
from datetime import datetime

DB_PATH = "/opt/ai-support-agent/data/call_log.db"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS calls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        call_id TEXT,
        caller_number TEXT,
        called_number TEXT,
        language TEXT,
        verified INTEGER DEFAULT 0,
        employee_id TEXT,
        user_name TEXT,
        user_email TEXT,
        start_time TEXT,
        end_time TEXT,
        duration_seconds INTEGER,
        ticket_number TEXT,
        ticket_status TEXT,
        recording_file TEXT,
        outcome TEXT,
        ai_summary TEXT
    )
    """)

    conn.commit()
    conn.close()


def create_call(call_id, caller_number, called_number, language, recording_file=None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO calls (
        call_id, caller_number, called_number, language,
        start_time, recording_file, outcome
    )
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        call_id,
        caller_number,
        called_number,
        language,
        datetime.utcnow().isoformat(),
        recording_file,
        "in_progress"
    ))

    conn.commit()
    conn.close()


def update_call_result(call_id, ticket_number=None, outcome=None, ai_summary=None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
    UPDATE calls
    SET ticket_number = COALESCE(?, ticket_number),
        outcome = COALESCE(?, outcome),
        ai_summary = COALESCE(?, ai_summary),
        end_time = ?,
        duration_seconds = CAST((julianday(?) - julianday(start_time)) * 86400 AS INTEGER)
    WHERE call_id = ?
    """, (
        ticket_number,
        outcome,
        ai_summary,
        datetime.utcnow().isoformat(),
        datetime.utcnow().isoformat(),
        call_id
    ))

    conn.commit()
    conn.close()
