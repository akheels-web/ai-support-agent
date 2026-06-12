import csv
from app.config import CSV_USERS_FILE


def verify_user(employee_id):
    employee_id = str(employee_id).strip()

    with open(CSV_USERS_FILE, newline="", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)

        for row in reader:
            if str(row.get("employee_id", "")).strip() == employee_id:
                return {
                    "verified": True,
                    "employee_id": row.get("employee_id"),
                    "name": row.get("name"),
                    "email": row.get("email"),
                    "phone": row.get("phone"),
                    "department": row.get("department"),
                }

    return {
        "verified": False,
        "reason": "Employee ID not found"
    }
