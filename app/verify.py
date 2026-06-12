import csv
import os
import re
from functools import lru_cache
from difflib import SequenceMatcher
from dotenv import load_dotenv

load_dotenv("/opt/ai-support-agent/.env")

USERS_CSV = os.getenv("CSV_USERS_FILE", "/opt/ai-support-agent/data/users.csv")

TOKEN_MATCH_THRESHOLD = 72
FULL_NAME_MATCH_THRESHOLD = 75


@lru_cache(maxsize=1)
def _load_users():
    users = {}

    try:
        with open(USERS_CSV, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)

            for row in reader:
                employee_id = row.get("employee_id", "").strip()

                if employee_id:
                    aliases_raw = row.get("aliases", "").strip()
                    aliases = []

                    if aliases_raw:
                        aliases = [a.strip() for a in aliases_raw.split("|") if a.strip()]

                    users[employee_id] = {
                        "employee_id": employee_id,
                        "name": row.get("name", "").strip(),
                        "aliases": aliases,
                        "email": row.get("email", "").strip(),
                        "phone": row.get("phone", "").strip(),
                        "department": row.get("department", "").strip(),
                    }

    except FileNotFoundError:
        print(f"[VERIFY] users.csv not found at {USERS_CSV}")

    return users


def _normalize(value):
    value = str(value).lower().strip()
    value = re.sub(r"[^a-z0-9\u0600-\u06FF ]+", " ", value)
    value = " ".join(value.split())
    return value


def _similarity(a, b):
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio() * 100


def _token_match_score(provided_name, stored_name):
    provided_tokens = _normalize(provided_name).split()
    stored_tokens = _normalize(stored_name).split()

    if not provided_tokens or not stored_tokens:
        return 0

    matched = 0

    for stored_token in stored_tokens:
        best_score = 0

        for provided_token in provided_tokens:
            score = _similarity(provided_token, stored_token)
            if score > best_score:
                best_score = score

        if best_score >= TOKEN_MATCH_THRESHOLD:
            matched += 1

    return matched


def _name_matches(provided_name, stored_name, aliases=None):
    provided_name = _normalize(provided_name)
    stored_name = _normalize(stored_name)

    if not provided_name or not stored_name:
        return False

    if provided_name == stored_name:
        return True

    aliases = aliases or []

    for alias in aliases:
        alias_norm = _normalize(alias)

        if provided_name == alias_norm:
            return True

        if _similarity(provided_name, alias_norm) >= FULL_NAME_MATCH_THRESHOLD:
            return True

    if _similarity(provided_name, stored_name) >= FULL_NAME_MATCH_THRESHOLD:
        return True

    stored_tokens = stored_name.split()
    required_matches = min(2, len(stored_tokens))
    matched_tokens = _token_match_score(provided_name, stored_name)

    return matched_tokens >= required_matches


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

    if not _name_matches(employee_name, record["name"], record.get("aliases", [])):
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
``