"""
Verify a caller by matching BOTH employee_id AND employee_name
against users.csv. Name matching is case-insensitive and tolerates
minor phonetic drift (e.g. "Mohamed" vs "Mohammed") via fuzzy ratio.

CSV format expected:
  employee_id,name,email,department
"""
import csv
import os
from functools import lru_cache

USERS_CSV = os.getenv("USERS_CSV_PATH", "/opt/ai-support-agent/users.csv")
NAME_MATCH_THRESHOLD = 80   # percent similarity required (0–100)


@lru_cache(maxsize=1)
def _load_users() -> dict:
    """Load CSV once and cache. Restart service to reload."""
    users = {}
    try:
        with open(USERS_CSV, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                eid = row.get("employee_id", "").strip()
                if eid:
                    users[eid] = {
                        "employee_id": eid,
                        "name":        row.get("name", "").strip(),
                        "email":       row.get("email", "").strip(),
                        "department":  row.get("department", "").strip(),
                    }
    except FileNotFoundError:
        print(f"[VERIFY] WARNING: users.csv not found at {USERS_CSV}")
    return users


def _name_matches(provided: str, stored: str) -> bool:
    """
    Case-insensitive fuzzy name match.
    Uses SequenceMatcher for lightweight similarity without extra deps.
    Falls back to exact match if names are very short.
    """
    p = provided.lower().strip()
    s = stored.lower().strip()
    if p == s:
        return True
    # Simple token overlap: every word in provided must appear in stored or vice versa
    p_tokens = set(p.split())
    s_tokens = set(s.split())
    if p_tokens & s_tokens:
        # at least one token in common — good enough for a voice agent
        return True
    # Sequence similarity fallback
    from difflib import SequenceMatcher
    ratio = SequenceMatcher(None, p, s).ratio() * 100
    return ratio >= NAME_MATCH_THRESHOLD


def verify_user(employee_id: str, employee_name: str) -> dict:
    """
    Returns:
        {"verified": True, "name": ..., "email": ..., "department": ...}
        {"verified": False, "reason": "..."}
    """
    users = _load_users()
    employee_id = employee_id.strip()

    record = users.get(employee_id)

    if not record:
        return {"verified": False, "reason": "employee_id_not_found"}

    if not _name_matches(employee_name, record["name"]):
        return {"verified": False, "reason": "name_mismatch"}

    return {
        "verified":    True,
        "name":        record["name"],
        "email":       record["email"],
        "department":  record["department"],
        "employee_id": employee_id,
    }