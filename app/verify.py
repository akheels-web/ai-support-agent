import csv
import os
import re
import time
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

# Multi-word compound numbers (11-19)
ARABIC_MULTI_WORDS = {
    "أحد عشر": 11, "احد عشر": 11, "إحدى عشر": 11, "إحدى عشرة": 11, "احدا عشر": 11,
    "اثنا عشر": 12, "اثني عشر": 12, "إثنا عشر": 12, "إثني عشر": 12, "اثناعشر": 12, "إثناعشر": 12,
    "ثلاثة عشر": 13, "ثلاثةعشر": 13, "ثلاثعشر": 13, "ثلاثطعش": 13, "تلطعش": 13,
    "أربعة عشر": 14, "اربعة عشر": 14, "اربعطعش": 14, "أربعطعش": 14,
    "خمسة عشر": 15, "خمسطعش": 15, "خمستعش": 15,
    "ستة عشر": 16, "ستطعش": 16, "ستتعش": 16,
    "سبعة عشر": 17, "سبعطعش": 17,
    "ثمانية عشر": 18, "ثمانطعش": 18,
    "تسعة عشر": 19, "تسعطعش": 19,
}

WORD_TO_DIGIT_VAL = {
    # English single digits
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9,
    # English teens & tens
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100, "thousand": 1000,
    # Arabic single digits
    "صفر": 0, "واحد": 1, "واحده": 1, "واحدة": 1, "إحدى": 1, "احدى": 1,
    "اثنين": 2, "إثنين": 2, "اثنان": 2, "إثنان": 2,
    "ثلاثة": 3, "ثلاث": 3,
    "اربعة": 4, "أربعة": 4, "اربع": 4, "أربع": 4,
    "خمسة": 5, "خمس": 5,
    "ستة": 6, "ست": 6,
    "سبعة": 7, "سبع": 7,
    "ثمانية": 8, "ثمان": 8, "ثماني": 8,
    "تسعة": 9, "تسع": 9,
    # Arabic teens (colloquial single token)
    "عشرة": 10, "عشر": 10,
    "احداعش": 11, "حداعش": 11,
    "اثنعش": 12, "طنعش": 12,
    "ثلاثطعش": 13, "تلطعش": 13,
    "اربعطعش": 14, "خمسطعش": 15, "خمستعش": 15,
    "ستطعش": 16, "ستتعش": 16, "سبعطعش": 17,
    "ثمانطعش": 18, "تسعطعش": 19,
    # Arabic tens
    "عشرون": 20, "عشرين": 20,
    "ثلاثون": 30, "ثلاثين": 30,
    "اربعون": 40, "أربعون": 40, "اربعين": 40, "أربعين": 40,
    "خمسون": 50, "خمسين": 50,
    "ستون": 60, "ستين": 60,
    "سبعون": 70, "سبعين": 70,
    "ثمانون": 80, "ثمانين": 80,
    "تسعون": 90, "تسعين": 90,
    # Arabic hundreds & thousands
    "مائة": 100, "مئة": 100, "ميه": 100,
    "مئتان": 200, "مئتين": 200,
    "ثلاثمائة": 300, "ثلاثمئة": 300, "ثلاثمية": 300,
    "أربعمائة": 400, "اربعمائة": 400, "خمسمائة": 500,
    "ألف": 1000, "الف": 1000, "ألفين": 2000, "الفين": 2000,
}


def normalize_digits(value: str) -> str:
    """
    Normalizes spoken digit phrases and compound numbers into numeric strings.
    E.g. "one zero zero two" -> "1002"
         "واحد صفر صفر اثنين" -> "1002"
         "أحد عشر" -> "11"
         "ألف واثنين" -> "1002"
         "twenty five" -> "25"
         "خمسة وعشرين" -> "25"
    """
    if not value:
        return ""
    value = str(value).lower().strip()

    # Fast path: check if raw string is already purely ASCII digits
    if value.isdigit():
        return value

    # Pre-process multi-word Arabic compounds (e.g. "أحد عشر" -> "11")
    s = value
    for phrase, num in ARABIC_MULTI_WORDS.items():
        s = s.replace(phrase, str(num))
    s = s.replace("-", " ")

    words = s.split()
    tokens = []

    for w in words:
        if w.isdigit():
            tokens.append(int(w))
            continue
        # Strip Arabic conjunction prefix 'و' (e.g. "وعشرين" -> "عشرين", "واثنين" -> "اثنين")
        if w.startswith("و") and len(w) > 1 and w[1:] in WORD_TO_DIGIT_VAL:
            w = w[1:]
        if w in ("and", "و"):
            continue
        if w in WORD_TO_DIGIT_VAL:
            tokens.append(WORD_TO_DIGIT_VAL[w])

    if not tokens:
        # Fallback to extracting any embedded digits
        digits_only = re.sub(r"\D", "", value)
        return digits_only if digits_only else value

    # If all tokens are single digits (0-9) and multiple words, concatenate (e.g. "1 0 0 2" -> "1002")
    if len(tokens) > 1 and all(0 <= t <= 9 for t in tokens):
        return "".join(str(t) for t in tokens)

    # If single token, return it as string
    if len(tokens) == 1:
        return str(tokens[0])

    # Evaluate composite summation (e.g. 1000 + 2 -> 1002, 20 + 5 -> 25)
    total = 0
    current = 0
    for num in tokens:
        if num == 1000:
            current = (1 if current == 0 else current) * 1000
            total += current
            current = 0
        elif num == 100:
            current = (1 if current == 0 else current) * 100
        else:
            current += num
    total += current
    return str(total)


def _normalize_phone(phone: str) -> str:
    if not phone:
        return ""
    digits = re.sub(r"\D", "", str(phone))
    return digits[-8:] if len(digits) >= 8 else digits


_USERS_CACHE = None
_USERS_CACHE_TIME = 0.0
_USERS_CACHE_TTL = 15.0  # seconds - auto-refreshes caller roster without daemon restarts


def clear_cache():
    """Manually invalidates the caller directory cache."""
    global _USERS_CACHE, _USERS_CACHE_TIME
    _USERS_CACHE = None
    _USERS_CACHE_TIME = 0.0


def _load_users():
    """
    Loads verified enterprise users with a 15-second TTL cache.
    Ensures that caller additions, deactivations, and VIP tier changes
    reflect across separate processes without requiring daemon restarts.
    """
    global _USERS_CACHE, _USERS_CACHE_TIME
    now = time.time()
    if _USERS_CACHE is not None and (now - _USERS_CACHE_TIME) < _USERS_CACHE_TTL:
        return _USERS_CACHE

    users = {}

    # 1. Attempt loading from app.db callers table
    try:
        from app.db import get_db
        with get_db() as conn:
            rows = conn.execute("SELECT * FROM callers ORDER BY id ASC").fetchall()
        if rows:
            for row in rows:
                employee_id = str(row.get("employee_id", "")).strip()
                if not employee_id:
                    continue

                aliases_raw = str(row.get("aliases") or "").strip()
                aliases = [a.strip() for a in aliases_raw.split("|") if a.strip()] if aliases_raw else []

                vip = bool(row.get("vip", 0))
                role = str(row.get("role") or "Employee").strip()
                tier = str(row.get("tier") or "").strip().upper()
                if not tier:
                    tier = "P1_VIP" if vip else "STANDARD"

                active = bool(row.get("active", 1))

                users[employee_id] = {
                    "employee_id": employee_id,
                    "name": str(row.get("name") or "").strip(),
                    "aliases": aliases,
                    "email": str(row.get("email") or "").strip(),
                    "phone": str(row.get("phone") or "").strip(),
                    "department": str(row.get("department") or "").strip(),
                    "vip": vip,
                    "role": role,
                    "tier": tier,
                    "active": active,
                    "is_executive": (tier == "P0_EXECUTIVE"),
                }
            _USERS_CACHE = users
            _USERS_CACHE_TIME = now
            return users
    except Exception:
        pass

    # 2. Fallback to USERS_CSV
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

                active_raw = str(row.get("active", "true")).strip().lower()
                active = active_raw not in ("false", "0", "no")

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
                    "active": active,
                    "is_executive": (tier == "P0_EXECUTIVE"),
                }

    except FileNotFoundError:
        print(f"[VERIFY] users.csv not found at {USERS_CSV}")

    _USERS_CACHE = users
    _USERS_CACHE_TIME = now
    return users


def lookup_caller_by_phone(phone_number: str):
    """
    Matches incoming caller ID against registered user phone numbers.
    Skips deactivated / offboarded callers.
    Returns matched user record or None.
    """
    if not phone_number:
        return None

    normalized_input = _normalize_phone(phone_number)
    if not normalized_input:
        return None

    users = _load_users()
    for user in users.values():
        if not user.get("active", True):
            continue
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

    # Offboarding Check: reject deactivated / departed accounts
    if not record.get("active", True):
        return {
            "verified": False,
            "reason": "account_deactivated",
            "message": "This employee account has been deactivated. Please contact IT Helpdesk administration.",
        }

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
        "active": record.get("active", True),
        "is_executive": record.get("is_executive", False),
    }