#!/usr/bin/env python3
"""
Migrate legacy call recordings to formatted name: username_time_day_year.wav
Updates files on disk and records in SQLite database.
"""
import os
import re
import time
from datetime import datetime
from pathlib import Path
import sqlite3

RECORDING_DIR = Path(os.getenv("RECORDING_DIR", "/var/spool/asterisk/monitor/ai-support"))
DB_PATH = Path(os.getenv("DATABASE_PATH", "/opt/ai-support-agent/data/calls.db"))

def migrate_recordings():
    if not RECORDING_DIR.exists():
        print(f"Recording directory {RECORDING_DIR} does not exist.")
        return

    conn = None
    if DB_PATH.exists():
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row

    call_lookup = {}
    if conn:
        try:
            rows = conn.execute("SELECT call_id, caller_number, employee_id, verified_name, recording_file, start_time FROM calls").fetchall()
            for r in rows:
                if r["recording_file"]:
                    call_lookup[Path(r["recording_file"]).name] = r
                if r["call_id"]:
                    call_lookup[r["call_id"]] = r
        except Exception as e:
            print(f"DB read error: {e}")

    for file in RECORDING_DIR.glob("*.wav"):
        stem = file.stem
        # If already migrated, skip
        parts = stem.split("_")
        if len(parts) >= 4 and len(parts[-1]) == 4 and parts[-1].isdigit() and not stem.startswith("call_"):
            print(f"Skipping already formatted: {file.name}")
            continue

        matched_call = call_lookup.get(file.name)
        if not matched_call:
            for k, v in call_lookup.items():
                if len(str(k)) > 8 and str(k) in stem:
                    matched_call = v
                    break

        username = None
        caller = "caller"
        start_time = int(file.stat().st_mtime)

        if matched_call:
            username = matched_call["verified_name"] or matched_call["employee_id"] or matched_call["caller_number"]
            if matched_call["start_time"]:
                start_time = int(matched_call["start_time"])
        else:
            if "_" in stem:
                p = stem.split("_")
                if len(p) >= 3:
                    caller = p[2]
            elif "-" in stem:
                p = stem.split("-")
                if len(p) >= 4:
                    caller = p[-2]
            username = caller

        clean_user = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', str(username or caller)).strip('_')
        if not clean_user:
            clean_user = "caller"

        dt = datetime.fromtimestamp(start_time)
        time_str = dt.strftime("%H-%M-%S")
        day_str = dt.strftime("%A")
        year_str = dt.strftime("%Y")

        new_name = f"{clean_user}_{time_str}_{day_str}_{year_str}.wav"
        new_path = file.parent / new_name

        if new_path != file:
            try:
                file.rename(new_path)
                print(f"Renamed: {file.name} -> {new_name}")
                if conn:
                    conn.execute(
                        "UPDATE calls SET recording_file=? WHERE recording_file LIKE ? OR call_id LIKE ?",
                        (str(new_path), f"%{file.name}%", f"%{file.stem}%")
                    )
                    conn.commit()
            except Exception as e:
                print(f"Failed to rename {file.name}: {e}")

    if conn:
        conn.close()
    print("Recordings migration finished.")

if __name__ == "__main__":
    migrate_recordings()
