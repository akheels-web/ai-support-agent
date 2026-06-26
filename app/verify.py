import csv
import os
import re
from functools import lru_cache
from difflib import SequenceMatcher
from dotenv import load_dotenv

load_dotenv("/opt/ai-support-agent/.env", override=True)

USERS_CSV = os.getenv("CSV_USERS_FILE", "/opt/ai-support-agent/data/users.csv")

FULL_NAME_MATCH_THRESHOLD = 78
FIRST_NAME_MATCH_THRESHOLD = 82

COMMON_TOKENS = {
    "al", "bin", "bint", "ibn", "abu", "umm",
    "ال", "بن", "بنت", "ابن", "أبو", "ابو", "ام", "أم"
}


@lru_cache(maxsize=1)
def _load_users():
    users = {}

    try:
        with open(USERS_CSV, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)

            for row in reader:
                employee_id = row.get("employee_id", "").strip()

                if not employee_id:
                    continue

                aliases_raw = row.get("aliases", "").strip()
                aliases = [a.strip() for a in aliases_raw.split("|") if a.strip()] if aliases_raw else []

                vip_raw = str(row.get("vip", "false")).strip().lower()
                vip = vip_raw in ("true", "1", "yes", "y")

                users[employee_id] = {
                    "employee_id": employee_id,
                    "name": row.get("name", "").strip(),
                    "aliases": aliases,
                    "email": row.get("email", "").strip(),
                    "phone": row.get("phone", "").strip(),
                    "department": row.get("department", "").strip(),
                    "vip": vip,
                }

    except FileNotFoundError:
        print(f"[VERIFY] users.csv not found at {USERS_CSV}")

    return users


def clear_cache():
    _load_users.cache_clear()


def _normalize(value):
    value = str(value).lower().strip()
    value = re.sub(r"[^a-z0-9\u0600-\u06FF ]+", " ", value)
    value = " ".join(value.split())
    return value


def _tokens(value):
    tokens = _normalize(value).split()
    return [t for t in tokens if t and t not in COMMON_TOKENS]


def _similarity(a, b):
    a = _normalize(a)
    b = _normalize(b)

    if not a or not b:
        return 0

    return SequenceMatcher(None, a, b).ratio() * 100


def _first_name(value):
    tokens = _tokens(value)
    return tokens[0] if tokens else ""


def _first_name_matches(provided_name, official_name, aliases):
    provided_first = _first_name(provided_name)

    if not provided_first:
        return False

    for candidate in [official_name] + (aliases or []):
        candidate_first = _first_name(candidate)

        if not candidate_first:
            continue

        if provided_first == candidate_first:
            return True

        if _similarity(provided_first, candidate_first) >= FIRST_NAME_MATCH_THRESHOLD:
            return True

    return False


def _full_name_matches(provided_name, official_name, aliases):
    provided = _normalize(provided_name)

    if not provided:
        return False

    for candidate in [official_name] + (aliases or []):
        candidate_norm = _normalize(candidate)

        if not candidate_norm:
            continue

        if provided == candidate_norm:
            return True

        if _similarity(provided, candidate_norm) >= FULL_NAME_MATCH_THRESHOLD:
            return True

    return False


def _secure_name_match(provided_name, official_name, aliases):
    first_ok = _first_name_matches(provided_name, official_name, aliases)
    full_ok = _full_name_matches(provided_name, official_name, aliases)
    return first_ok and full_ok


def verify_user(employee_id, employee_name):
    users = _load_users()

    employee_id = str(employee_id).strip()
    employee_name = str(employee_name).strip()

    if not employee_id:
        return {"verified": False, "reason": "employee_id_missing"}

    if not employee_name:
        return {"verified": False, "reason": "employee_name_missing"}

    record = users.get(employee_id)

    if not record:
        return {"verified": False, "reason": "employee_id_not_found"}

    official_name = record.get("name", "")
    aliases = record.get("aliases", [])

    if not _secure_name_match(employee_name, official_name, aliases):
        print(
            f"[VERIFY] Name mismatch. Provided='{employee_name}', "
            f"Expected='{official_name}', EmployeeID='{employee_id}'"
        )
        return {"verified": False, "reason": "name_mismatch"}

    return {
        "verified": True,
        "employee_id": record["employee_id"],
        "name": record["name"],
        "email": record["email"],
        "phone": record["phone"],
        "department": record["department"],
        "vip": record.get("vip", False),
    }