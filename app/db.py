"""
Enterprise Database Client for National Finance AI Support Agent.

Supports:
1. PostgreSQL 16 via psycopg (v3) or psycopg2 with connection pooling for production.
2. SQLite with WAL mode and thread-locking for local testing and offline fallback.
3. Atomic rate limiting, transparent parameter normalization (? -> %s), and composite indexing.
"""

import os
import re
import time
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("app.db")

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "dashboard.db"))
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "2"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "20"))

# Driver detection
POSTGRES_AVAILABLE = False
POSTGRES_V3 = False
POSTGRES_V2 = False

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    POSTGRES_AVAILABLE = True
    POSTGRES_V3 = True
except ImportError:
    try:
        import psycopg2
        from psycopg2.pool import ThreadedConnectionPool
        from psycopg2.extras import RealDictCursor
        POSTGRES_AVAILABLE = True
        POSTGRES_V2 = True
    except ImportError:
        pass

import sqlite3


class RowDict(dict):
    """
    Dictionary row subclass that also allows integer index lookups (e.g. row[0]),
    providing 100% backward compatibility between sqlite3.Row and Postgres dicts.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._list = list(self.values())

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._list[key]
        return super().__getitem__(key)


class DatabaseManager:
    """
    Thread-safe dual-engine database connection and pooling manager.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._pool = None
        self._engine = "sqlite"
        self._initialized = False

        self._configure_engine()

    def _configure_engine(self):
        url = DATABASE_URL.lower()
        if (url.startswith("postgres://") or url.startswith("postgresql://")) and POSTGRES_AVAILABLE:
            try:
                if POSTGRES_V3:
                    self._pool = ConnectionPool(
                        conninfo=DATABASE_URL,
                        min_size=DB_POOL_MIN,
                        max_size=DB_POOL_MAX,
                        kwargs={"row_factory": dict_row},
                    )
                elif POSTGRES_V2:
                    self._pool = ThreadedConnectionPool(
                        minconn=DB_POOL_MIN,
                        maxconn=DB_POOL_MAX,
                        dsn=DATABASE_URL,
                    )
                self._engine = "postgres"
                logger.info(f"[DB] Initialized PostgreSQL 16 connection pool (min={DB_POOL_MIN}, max={DB_POOL_MAX})")
                return
            except Exception as exc:
                logger.warning(f"[DB] Failed to connect to PostgreSQL ({exc}). Falling back to SQLite.")

        # Default or fallback to SQLite
        self._engine = "sqlite"
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"[DB] Operating in SQLite mode: {DB_PATH}")

    @property
    def engine(self) -> str:
        return self._engine

    def get_connection(self):
        """
        Returns a wrapped database connection.
        Must be closed or used in a context manager to release connection back to pool.
        """
        if self._engine == "postgres":
            if POSTGRES_V3:
                raw_conn = self._pool.getconn()
                return PostgresConnectionWrapper(raw_conn, self._pool, is_v3=True)
            elif POSTGRES_V2:
                raw_conn = self._pool.getconn()
                return PostgresConnectionWrapper(raw_conn, self._pool, is_v3=False)
        else:
            Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(DB_PATH, timeout=15)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            return SQLiteConnectionWrapper(conn)


class PostgresCursorWrapper:
    """Wraps Postgres cursor to normalize return values to RowDict."""

    def __init__(self, cursor, is_v3=True):
        self._cur = cursor
        self._is_v3 = is_v3

    def fetchone(self) -> Optional[RowDict]:
        row = self._cur.fetchone()
        if row is None:
            return None
        return RowDict(row)

    def fetchall(self) -> List[RowDict]:
        rows = self._cur.fetchall()
        return [RowDict(r) for r in rows]

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    def close(self):
        try:
            self._cur.close()
        except Exception:
            pass


class PostgresConnectionWrapper:
    """Connection wrapper for PostgreSQL with connection pooling."""

    def __init__(self, raw_conn, pool, is_v3=True):
        self._conn = raw_conn
        self._pool = pool
        self._is_v3 = is_v3
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()

    def _normalize_query(self, query: str) -> str:
        # Convert ? parameter markers to %s for Postgres
        normalized = re.sub(r"\?", "%s", query)
        # Normalize sqlite auto-increment to serial if accidentally passed
        normalized = normalized.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        normalized = normalized.replace("PRAGMA table_info", "-- PRAGMA table_info")
        return normalized

    def execute(self, query: str, params: Optional[Union[Tuple, List]] = None):
        sql = self._normalize_query(query)
        if self._is_v3:
            cur = self._conn.cursor(row_factory=dict_row)
        else:
            cur = self._conn.cursor(cursor_factory=RealDictCursor)

        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        return PostgresCursorWrapper(cur, is_v3=self._is_v3)

    def commit(self):
        if not self._closed and self._conn:
            self._conn.commit()

    def rollback(self):
        if not self._closed and self._conn:
            self._conn.rollback()

    def close(self):
        if not self._closed:
            self._closed = True
            if self._pool and self._conn:
                try:
                    self._pool.putconn(self._conn)
                except Exception as e:
                    logger.debug(f"[DB] Error returning connection to pool: {e}")
                self._conn = None


class SQLiteConnectionWrapper:
    """Connection wrapper for SQLite."""

    def __init__(self, raw_conn: sqlite3.Connection):
        self._conn = raw_conn
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()

    def execute(self, query: str, params: Optional[Union[Tuple, List]] = None):
        if params:
            cur = self._conn.execute(query, params)
        else:
            cur = self._conn.execute(query)
        return SQLiteCursorWrapper(cur)

    def commit(self):
        if not self._closed and self._conn:
            self._conn.commit()

    def rollback(self):
        if not self._closed and self._conn:
            self._conn.rollback()

    def close(self):
        if not self._closed:
            self._closed = True
            try:
                self._conn.close()
            except Exception:
                pass


class SQLiteCursorWrapper:
    """Wraps SQLite cursor so results are RowDict objects."""

    def __init__(self, cursor: sqlite3.Cursor):
        self._cur = cursor

    def fetchone(self) -> Optional[RowDict]:
        row = self._cur.fetchone()
        if row is None:
            return None
        return RowDict(dict(row))

    def fetchall(self) -> List[RowDict]:
        rows = self._cur.fetchall()
        return [RowDict(dict(r)) for r in rows]

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    def close(self):
        try:
            self._cur.close()
        except Exception:
            pass


# Global database manager instance
_db_manager = DatabaseManager()


def get_db():
    """
    Obtain a database connection.
    Usage:
        conn = get_db()
        try:
            rows = conn.execute("SELECT * FROM users").fetchall()
        finally:
            conn.close()

    Or via context manager:
        with get_db() as conn:
            conn.execute("INSERT ...")
    """
    return _db_manager.get_connection()


def get_engine() -> str:
    """Returns 'postgres' or 'sqlite'."""
    return _db_manager.engine


def execute(query: str, params: Optional[Union[Tuple, List]] = None) -> Any:
    """Execute query with automatic commit and connection cleanup."""
    with get_db() as conn:
        cur = conn.execute(query, params)
        conn.commit()
        return cur


def fetch_one(query: str, params: Optional[Union[Tuple, List]] = None) -> Optional[RowDict]:
    """Fetch single row as dictionary."""
    with get_db() as conn:
        cur = conn.execute(query, params)
        return cur.fetchone()


def fetch_all(query: str, params: Optional[Union[Tuple, List]] = None) -> List[RowDict]:
    """Fetch all matching rows as list of dictionaries."""
    with get_db() as conn:
        cur = conn.execute(query, params)
        return cur.fetchall()


def atomic_check_rate_limit(key: str, limit: int, window_seconds: int, lock_seconds: int) -> Dict[str, Any]:
    """
    Thread-safe and process-safe atomic rate limiting check.
    Eliminates race conditions between concurrent calls.
    """
    now = int(time.time())

    with get_db() as conn:
        # Check existing rate limit record
        row = conn.execute("SELECT counter, window_start, locked_until FROM rate_limits WHERE key = ?", (key,)).fetchone()

        if row:
            locked_until = int(row.get("locked_until") or 0)
            if locked_until > now:
                return {
                    "allowed": False,
                    "reason": "locked",
                    "retry_after": locked_until - now,
                }

            window_start = int(row.get("window_start") or now)
            counter = int(row.get("counter") or 0)

            if now - window_start > window_seconds:
                counter = 1
                window_start = now
                locked_until = 0
            else:
                counter += 1

            if counter > limit:
                locked_until = now + lock_seconds

            conn.execute(
                """
                UPDATE rate_limits
                SET counter = ?, window_start = ?, locked_until = ?
                WHERE key = ?
                """,
                (counter, window_start, locked_until, key),
            )

            if counter > limit:
                return {
                    "allowed": False,
                    "reason": "limit_exceeded",
                    "retry_after": lock_seconds,
                }

            return {"allowed": True, "remaining": max(0, limit - counter)}

        else:
            # Key does not exist yet
            conn.execute(
                """
                INSERT INTO rate_limits (key, counter, window_start, locked_until)
                VALUES (?, 1, ?, 0)
                """,
                (key, now),
            )
            return {"allowed": True, "remaining": limit - 1}


def _ensure_column(conn, table: str, column: str, definition: str):
    """Adds column if missing (SQLite specific migration helper) with strict identifier sanitization."""
    if not re.match(r"^[a-zA-Z0-9_]+$", table) or not re.match(r"^[a-zA-Z0-9_]+$", column):
        raise ValueError(f"Invalid SQL identifier in _ensure_column: {table}.{column}")
    if not re.match(r"^[a-zA-Z0-9_() ]+$", definition):
        raise ValueError(f"Invalid SQL column definition: {definition}")

    if _db_manager.engine == "sqlite":
        try:
            columns = conn.execute(f"PRAGMA table_info({table})").fetchall()
            existing = {r["name"] if isinstance(r, dict) else r[1] for r in columns}
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        except Exception as e:
            logger.debug(f"[DB] Column check on {table}.{column}: {e}")


def init_all_tables():
    """
    Initializes all production database tables and composite indexes.
    Idempotent and safe to run on application startup.
    """
    engine = _db_manager.engine

    with get_db() as conn:
        if engine == "postgres":
            # PostgreSQL 16 schema
            conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(100) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                role VARCHAR(50) NOT NULL,
                active SMALLINT DEFAULT 1,
                created_at BIGINT NOT NULL
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS calls (
                id SERIAL PRIMARY KEY,
                call_id VARCHAR(120) UNIQUE NOT NULL,
                caller_number VARCHAR(50),
                called_number VARCHAR(50),
                employee_id VARCHAR(50),
                verified_name VARCHAR(150),
                language VARCHAR(10),
                start_time BIGINT,
                end_time BIGINT,
                duration_seconds INTEGER,
                status VARCHAR(50),
                ticket_number VARCHAR(100),
                ticket_created SMALLINT DEFAULT 0,
                recording_file VARCHAR(255),
                summary TEXT,
                is_vip SMALLINT DEFAULT 0,
                transferred SMALLINT DEFAULT 0,
                transfer_target VARCHAR(50),
                tier VARCHAR(50) DEFAULT 'STANDARD',
                escalation_reason TEXT,
                resolution_type VARCHAR(50),
                ai_deflected SMALLINT DEFAULT 0
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS security_events (
                id SERIAL PRIMARY KEY,
                event_type VARCHAR(100) NOT NULL,
                key VARCHAR(150),
                details TEXT,
                created_at BIGINT NOT NULL
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS rate_limits (
                key VARCHAR(150) PRIMARY KEY,
                counter INTEGER DEFAULT 0,
                window_start BIGINT,
                locked_until BIGINT DEFAULT 0
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS quality_reviews (
                id SERIAL PRIMARY KEY,
                recording_file VARCHAR(255),
                reviewer VARCHAR(100),
                rating VARCHAR(50),
                notes TEXT,
                created_at BIGINT NOT NULL
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key VARCHAR(100) PRIMARY KEY,
                value TEXT,
                updated_at BIGINT NOT NULL
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id SERIAL PRIMARY KEY,
                username VARCHAR(100) NOT NULL,
                action VARCHAR(100) NOT NULL,
                entity_type VARCHAR(100),
                entity_id VARCHAR(100),
                created_at BIGINT NOT NULL,
                prev_hash VARCHAR(64),
                record_hash VARCHAR(64)
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token VARCHAR(128) PRIMARY KEY,
                username VARCHAR(100) NOT NULL,
                created_at BIGINT NOT NULL,
                expires_at BIGINT NOT NULL
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS prompt_versions (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100),
                greeting TEXT,
                system_prompt TEXT,
                active SMALLINT DEFAULT 0,
                created_by VARCHAR(100),
                created_at BIGINT NOT NULL
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS callers (
                id SERIAL PRIMARY KEY,
                employee_id VARCHAR(50) UNIQUE NOT NULL,
                name VARCHAR(150) NOT NULL,
                aliases TEXT,
                email VARCHAR(150),
                phone VARCHAR(50),
                department VARCHAR(100),
                role VARCHAR(100) DEFAULT 'Employee',
                vip SMALLINT DEFAULT 0,
                tier VARCHAR(50) DEFAULT 'STANDARD',
                active SMALLINT DEFAULT 1,
                source VARCHAR(50) DEFAULT 'manual',
                updated_at BIGINT NOT NULL,
                created_at BIGINT NOT NULL
            );
            """)

            # Composite indexes for high concurrency
            conn.execute("CREATE INDEX IF NOT EXISTS idx_calls_call_id ON calls(call_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_calls_start_status ON calls(start_time, status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_calls_caller ON calls(caller_number);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_calls_tier ON calls(tier);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_security_created ON security_events(created_at);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at);")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_callers_emp_id ON callers(employee_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_callers_phone ON callers(phone);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_callers_active ON callers(active);")
            _ensure_column(conn, "audit_logs", "prev_hash", "VARCHAR(64)")
            _ensure_column(conn, "audit_logs", "record_hash", "VARCHAR(64)")

        else:
            # SQLite schema
            conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE,
                password_hash TEXT,
                role TEXT,
                active INTEGER DEFAULT 1,
                created_at INTEGER
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                call_id TEXT UNIQUE,
                caller_number TEXT,
                called_number TEXT,
                employee_id TEXT,
                verified_name TEXT,
                language TEXT,
                start_time INTEGER,
                end_time INTEGER,
                duration_seconds INTEGER,
                status TEXT,
                ticket_number TEXT,
                ticket_created INTEGER DEFAULT 0,
                recording_file TEXT,
                summary TEXT
            );
            """)

            _ensure_column(conn, "calls", "is_vip", "INTEGER DEFAULT 0")
            _ensure_column(conn, "calls", "transferred", "INTEGER DEFAULT 0")
            _ensure_column(conn, "calls", "transfer_target", "TEXT")
            _ensure_column(conn, "calls", "tier", "TEXT DEFAULT 'STANDARD'")
            _ensure_column(conn, "calls", "escalation_reason", "TEXT")
            _ensure_column(conn, "calls", "resolution_type", "TEXT")
            _ensure_column(conn, "calls", "ai_deflected", "INTEGER DEFAULT 0")

            conn.execute("""
            CREATE TABLE IF NOT EXISTS security_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT,
                key TEXT,
                details TEXT,
                created_at INTEGER
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS rate_limits (
                key TEXT PRIMARY KEY,
                counter INTEGER DEFAULT 0,
                window_start INTEGER,
                locked_until INTEGER DEFAULT 0
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS quality_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recording_file TEXT,
                reviewer TEXT,
                rating TEXT,
                notes TEXT,
                created_at INTEGER
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at INTEGER
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT,
                action TEXT,
                entity_type TEXT,
                entity_id TEXT,
                created_at INTEGER,
                prev_hash TEXT,
                record_hash TEXT
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                username TEXT,
                created_at INTEGER,
                expires_at INTEGER
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS prompt_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                greeting TEXT,
                system_prompt TEXT,
                active INTEGER DEFAULT 0,
                created_by TEXT,
                created_at INTEGER
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS callers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                aliases TEXT,
                email TEXT,
                phone TEXT,
                department TEXT,
                role TEXT DEFAULT 'Employee',
                vip INTEGER DEFAULT 0,
                tier TEXT DEFAULT 'STANDARD',
                active INTEGER DEFAULT 1,
                source TEXT DEFAULT 'manual',
                updated_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            );
            """)

            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_calls_call_id ON calls(call_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_calls_start_status ON calls(start_time, status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_calls_caller ON calls(caller_number);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_calls_tier ON calls(tier);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_security_created ON security_events(created_at);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at);")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_callers_emp_id ON callers(employee_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_callers_phone ON callers(phone);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_callers_active ON callers(active);")
            _ensure_column(conn, "audit_logs", "prev_hash", "TEXT")
            _ensure_column(conn, "audit_logs", "record_hash", "TEXT")

        conn.commit()

    # Automatically seed callers from CSV on first run if table is empty
    seed_callers_from_csv_if_empty()

    logger.info(f"[DB] Initialized database schema successfully on {engine}.")


def seed_callers_from_csv_if_empty(conn=None):
    """
    If callers table is empty, seed it from USERS_CSV so existing
    telephony configurations are immediately active in the DB.
    """
    csv_file = os.getenv("CSV_USERS_FILE", str(BASE_DIR / "data" / "users.csv"))
    if not os.path.exists(csv_file):
        return

    should_close = False
    if conn is None:
        conn = get_db()
        should_close = True

    try:
        row = conn.execute("SELECT COUNT(*) as cnt FROM callers").fetchone()
        count = row["cnt"] if row else 0
        if count == 0:
            import csv
            now = int(time.time())
            with open(csv_file, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    emp_id = str(r.get("employee_id", "")).strip()
                    if not emp_id:
                        continue
                    vip_raw = str(r.get("vip", "false")).strip().lower()
                    vip = 1 if vip_raw in ("true", "1", "yes", "y") else 0
                    tier = str(r.get("tier", "")).strip().upper() or ("P1_VIP" if vip else "STANDARD")
                    active_raw = str(r.get("active", "1")).strip().lower()
                    active = 0 if active_raw in ("false", "0", "no") else 1

                    conn.execute(
                        """
                        INSERT INTO callers (
                            employee_id, name, aliases, email, phone,
                            department, role, vip, tier, active, source, updated_at, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            emp_id,
                            str(r.get("name", "")).strip(),
                            str(r.get("aliases", "")).strip(),
                            str(r.get("email", "")).strip(),
                            str(r.get("phone", "")).strip(),
                            str(r.get("department", "")).strip(),
                            str(r.get("role", "Employee")).strip(),
                            vip,
                            tier,
                            active,
                            "csv_seed",
                            now,
                            now,
                        ),
                    )
            conn.commit()
            logger.info(f"[DB] Seeded callers table from {csv_file}")
    except Exception as exc:
        logger.warning(f"[DB] Caller seed check failed: {exc}")
    finally:
        if should_close:
            conn.close()


def sync_callers_to_csv(csv_path=None):
    """
    Dumps all rows from callers table to USERS_CSV to keep file-based
    integrations and git repo backups strictly in sync.
    """
    csv_file = csv_path or os.getenv("CSV_USERS_FILE", str(BASE_DIR / "data" / "users.csv"))
    try:
        import csv
        Path(csv_file).parent.mkdir(parents=True, exist_ok=True)
        with get_db() as conn:
            rows = conn.execute(
                "SELECT employee_id, name, aliases, email, phone, department, vip, role, tier, active FROM callers ORDER BY id ASC"
            ).fetchall()

        with open(csv_file, "w", newline="", encoding="utf-8") as f:
            fieldnames = ["employee_id", "name", "aliases", "email", "phone", "department", "vip", "role", "tier", "active"]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in rows:
                writer.writerow({
                    "employee_id": r["employee_id"],
                    "name": r["name"],
                    "aliases": r["aliases"] or "",
                    "email": r["email"] or "",
                    "phone": r["phone"] or "",
                    "department": r["department"] or "",
                    "vip": "true" if r["vip"] else "false",
                    "role": r["role"] or "Employee",
                    "tier": r["tier"] or "STANDARD",
                    "active": "true" if r["active"] else "false",
                })
        logger.info(f"[DB] Synchronized {len(rows)} callers to {csv_file}")
        return True
    except Exception as exc:
        logger.error(f"[DB] Failed to sync callers to CSV: {exc}")
        return False

