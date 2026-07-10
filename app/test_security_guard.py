"""Runnable check for rate-limit / lockout logic. No framework.
Run: PYTHONPATH=/opt/ai-support-agent python -m app.test_security_guard
"""
import tempfile, os
from app import security_guard as sg

# Point the DB at a throwaway file so the test is side-effect free.
sg.DB_PATH = os.path.join(tempfile.mkdtemp(), "test.db")

KEY = "verify:1002"
LIMIT, WINDOW, LOCK = 3, 3600, 3600

# Not locked initially.
assert sg.is_locked(KEY) == 0

# First LIMIT attempts are allowed; the one past LIMIT trips the lock.
for i in range(LIMIT):
    assert sg.check_rate_limit(KEY, LIMIT, WINDOW, LOCK)["allowed"], f"attempt {i} should pass"

tripped = sg.check_rate_limit(KEY, LIMIT, WINDOW, LOCK)
assert not tripped["allowed"], "attempt past limit must be blocked"
assert tripped["reason"] == "rate_limited"

# is_locked now reports remaining time WITHOUT incrementing the counter.
remaining = sg.is_locked(KEY)
assert 0 < remaining <= LOCK, f"expected active lock, got {remaining}"
assert sg.is_locked(KEY) > 0, "is_locked must be read-only / idempotent"

# reset clears the lock (success path).
sg.reset_rate_limit(KEY)
assert sg.is_locked(KEY) == 0, "reset must clear the lock"
assert sg.check_rate_limit(KEY, LIMIT, WINDOW, LOCK)["allowed"], "allowed again after reset"

print("OK: rate-limit trips at limit, is_locked is read-only, reset clears it")
