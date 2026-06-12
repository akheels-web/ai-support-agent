import csv
import os
from functools import lru_cache
from difflib import SequenceMatcher
from dotenv import load_dotenv

load_dotenv("/opt/ai-support-agent/.env")

USERS_CSV = os.getenv("CSV_USERS_FILE", "/opt/ai-support-agent/data/users.csv")
NAME_MATCH_THRESHOLD = 85


@lru_cache(maxsize=1)
def _load_users():
    users = {}

    try:
        with open(USERS_CSV, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)

            for row in reader:
                employee_id = row.get("employee_id", "").strip()

                if employee_id:
                    users[employee_id] = {
                        "employee_id": employee_id,
                        "name": row.get("name", "").strip(),
                        "email": row.get("email", "").strip(),
                        "phone": row.get("phone", "").strip(),
                        "department": row.get("department", "").strip(),
                    }

    except FileNotFoundError:
        print(f"[VERIFY] users.csv not found at {USERS_CSV}")

    return users


def _normalize_name(value):
    return " ".join(str(value).lower().strip().split())


def _name_matches(provided_name, stored_name):
    provided = _normalize_name(provided_name)
    stored = _normalize_name(stored_name)

    if not provided or not stored:
        return False

    if provided == stored:
        return True

    provided_tokens = set(provided.split())
    stored_tokens = set(stored.split())

    # Stronger check: if stored name has 2+ words, require at least 2 matching words.
    if len(stored_tokens) >= 2:
        common_tokens = provided_tokens.intersection(stored_tokens)
        if len(common_tokens) >= 2:
            return True

    ratio = SequenceMatcher(None, provided, stored).ratio() * 100

    return ratio >= NAME_MATCH_THRESHOLD


def verify_user(employee_id, employee_name):
    users = _load_users()

    employee_id = str(employee_id).strip()
    employee_name = str(employee_name).strip()

    if not employee_id:
        return {
            "verified": False,
            "reason": "employee_id_missing"
        }

    if not employee_name:
        return {
            "verified": False,
            "reason": "employee_name_missing"
        }

    record = users.get(employee_id)

    if not record:
        return {
            "verified": False,
            "reason": "employee_id_not_found"
        }

    if not _name_matches(employee_name, record["name"]):
        return {
            "verified": False,
            "reason": "name_mismatch"
        }

    return {
        "verified": True,
        "employee_id": record["employee_id"],
        "name": record["name"],
        "email": record["email"],
        "phone": record["phone"],
        "department": record["department"],
    }