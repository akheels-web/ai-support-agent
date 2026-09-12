import csv
import os
import re
from pathlib import Path
from functools import lru_cache
from difflib import SequenceMatcher
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
env_path = os.getenv("ENV_FILE", str(BASE_DIR / ".env"))
load_dotenv(env_path, override=True)

USERS_CSV = os.getenv("CSV_USERS_FILE", str(BASE_DIR / "data" / "users.csv"))

FULL_NAME_MATCH_THRESHOLD = 76
FIRST_NAME_MATCH_THRESHOLD = 80

COMMON_TOKENS = {
    "al", "bin", "bint", "ibn", "abu", "umm",
    "ال", "بن", "بنت", "ابن", "أبو", "ابو", "ام", "أم"
}

WORD_TO_DIGIT = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "صفر": "0", "واحد": "1", "اثنين": "2", "إثنين": "2", "ثلاثة": "3",
    "اربعة": "4", "أربعة": "4", "خمسة": "5", "ستة": "6", "سبعة": "7",
    "ثمانية": "8", "تسعة": "9",
}


def normalize_digits(value: str) -> str:
    """
    Normalizes spoken digit phrases into numeric strings.
    E.g. "one zero zero two" -> "1002"
    """
    if not value:
        return ""
    value = str(value).lower().strip()
    # Check if raw value already is digits
    digits_only = re.sub(r"\D", "", value)
    if digits_only:
        return digits_only

    words = value.split()
    converted = []
    for w in words:
        if w in WORD_TO_DIGIT:
            converted.append(WORD_TO_DIGIT[w])
        elif w.isdigit():
            converted.append(w)
    return "".join(converted) if converted else value


def _normalize_phone(phone: str) -> str:
    if not phone:
        return ""
    digits = re.sub(r"\D", "", str(phone))
    return digits[-8:] if len(digits) >= 8 else digits


@lru_cache(maxsize=1)
def _load_users():
    users = {}

    try:
        with open(USERS_CSV, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)

            for row in reader:
                employee_id = str(row.get("employee_id", "")).strip()

                if not employee_id:
                    continue

                aliases_raw = row.get("aliases", "").strip()
                aliases = [a.strip() for a in aliases_raw.split("|") if a.strip()] if aliases_raw else []

                vip_raw = str(row.get("vip", "false")).strip().lower()
                vip = vip_raw in ("true", "1", "yes", "y")

                role = row.get("role", "Employee").strip()
                tier = row.get("tier", "").strip().upper()
                if not tier:
                    tier = "P1_VIP" if vip else "STANDARD"

                users[employee_id] = {
                    "employee_id": employee_id,
                    "name": row.get("name", "").strip(),
                    "aliases": aliases,
                    "email": row.get("email", "").strip(),
                    "phone": row.get("phone", "").strip(),
                    "department": row.get("department", "").strip(),
                    "vip": vip,
                    "role": role,
                    "tier": tier,
                    "is_executive": (tier == "P0_EXECUTIVE"),
                }

    except FileNotFoundError:
        print(f"[VERIFY] users.csv not found at {USERS_CSV}")

    return users


def clear_cache():
    _load_users.cache_clear()


def lookup_caller_by_phone(phone_number: str):
    """
    Matches incoming caller ID against registered user phone numbers.
    Returns matched user record or None.
    """
    if not phone_number:
        return None

    normalized_input = _normalize_phone(phone_number)
    if not normalized_input:
        return None

    users = _load_users()
    for user in users.values():
        if _normalize_phone(user.get("phone")) == normalized_input:
            return user

    return None


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

    normalized_id = normalize_digits(employee_id)
    employee_id = normalized_id or str(employee_id).strip()
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
        "role": record.get("role", "Employee"),
        "tier": record.get("tier", "STANDARD"),
        "is_executive": record.get("is_executive", False),
    }