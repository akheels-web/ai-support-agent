"""
Enterprise Active Directory (AD / LDAP) Sync Engine for National Finance.

Provides:
1. Secure LDAPS (Port 636) and LDAP/StartTLS (Port 389) connectivity with timeout guardrails.
2. Paged LDAP searches to safely stream > 1,000 corporate user objects without size limit errors.
3. userAccountControl bitmask evaluation (0x0002 ACCOUNTDISABLE) for instantaneous offboarding.
4. Non-destructive merge preserving custom spoken Arabic/English phonetic aliases.
5. In-flight diagnostic connection test tool with latency and bind verification.
6. Automatic bi-directional CSV synchronization and in-memory cache purging.
"""

import os
import re
import ssl
import time
import json
import logging
import threading
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path

import app.config as config
import app.db as db
from app.verify import _normalize_phone, clear_cache

logger = logging.getLogger("app.ad_sync")

# Optional ldap3 import with graceful fallback
LDAP3_AVAILABLE = False
try:
    import ldap3
    from ldap3 import Server, Connection, ALL, NTLM, SUBTREE, Tls
    from ldap3.core.exceptions import (
        LDAPException, LDAPSocketOpenError, LDAPBindError,
        LDAPInvalidCredentialsResult, LDAPNoSuchObjectResult
    )
    LDAP3_AVAILABLE = True
except ImportError:
    logger.warning("[AD_SYNC] 'ldap3' package is not installed. Active Directory sync will run in mock/manual mode.")


# -----------------------------------------------------------------------------
# Active Directory userAccountControl Bitmask Parser
# -----------------------------------------------------------------------------

# Reference: Microsoft MS-SAMR Section 2.2.1.13 (User Account Control Flags)
UAC_FLAGS = {
    0x0001: "SCRIPT",
    0x0002: "ACCOUNTDISABLE",
    0x0008: "HOMEDIR_REQUIRED",
    0x0010: "LOCKOUT",
    0x0020: "PASSWD_NOTREQD",
    0x0040: "PASSWD_CANT_CHANGE",
    0x0080: "ENCRYPTED_TEXT_PWD_ALLOWED",
    0x0100: "TEMP_DUPLICATE_ACCOUNT",
    0x0200: "NORMAL_ACCOUNT",
    0x0800: "INTERDOMAIN_TRUST_ACCOUNT",
    0x1000: "WORKSTATION_TRUST_ACCOUNT",
    0x2000: "SERVER_TRUST_ACCOUNT",
    0x10000: "DONT_EXPIRE_PASSWORD",
    0x20000: "MNS_LOGON_ACCOUNT",
    0x40000: "SMARTCARD_REQUIRED",
    0x80000: "TRUSTED_FOR_DELEGATION",
    0x100000: "NOT_DELEGATED",
    0x200000: "USE_DES_KEY_ONLY",
    0x400000: "DONT_REQ_PREAUTH",
    0x800000: "PASSWORD_EXPIRED",
    0x1000000: "TRUSTED_TO_AUTH_FOR_DELEGATION",
}


def parse_user_account_control(uac_value: Optional[int]) -> Dict[str, Any]:
    """
    Parses AD userAccountControl bitmask into flags and evaluates active/disabled status.
    If bit 0x0002 (ACCOUNTDISABLE) is present, the employee is inactive/offboarded.
    """
    if uac_value is None:
        return {"raw": 512, "is_disabled": False, "is_locked": False, "active": 1, "flags": ["NORMAL_ACCOUNT"]}

    try:
        uac = int(uac_value)
    except (ValueError, TypeError):
        uac = 512

    is_disabled = bool(uac & 0x0002)
    is_locked = bool(uac & 0x0010)
    flags = [name for mask, name in UAC_FLAGS.items() if (uac & mask)]

    return {
        "raw": uac,
        "is_disabled": is_disabled,
        "is_locked": is_locked,
        "active": 0 if is_disabled else 1,
        "flags": flags,
    }


# -----------------------------------------------------------------------------
# -----------------------------------------------------------------------------
# Active Directory Helpers: Bind Normalization & Error Code Translator
# -----------------------------------------------------------------------------

def normalize_bind_dn(bind_dn: Optional[str], base_dn: str = "") -> str:
    """
    Normalizes Active Directory bind username or DN.
    Handles inputs like:
      - 'ai.agent/' -> 'ai.agent@nfc.co.om' (when base_dn is DC=nfc,DC=co,DC=om)
      - 'ai.agent'  -> 'ai.agent@nfc.co.om'
      - 'nfc\\ai.agent' -> remains 'nfc\\ai.agent'
      - 'ai.agent@nfc.co.om' -> remains 'ai.agent@nfc.co.om'
      - 'CN=ai.agent,DC=nfc,DC=co,DC=om' -> remains DN
    """
    if not bind_dn:
        return ""
    clean = str(bind_dn).strip().rstrip("/")
    if not clean:
        return ""
    if "@" in clean or "\\" in clean or "=" in clean:
        return clean

    # If simple username without domain, derive UPN suffix from base_dn
    if base_dn:
        dc_parts = re.findall(r"DC=([^,]+)", base_dn, re.IGNORECASE)
        if dc_parts:
            domain = ".".join(dc_parts)
            return f"{clean}@{domain}"

    return clean


def explain_ad_error(raw_message: str) -> str:
    """
    Translates cryptic Active Directory LDAP / AcceptSecurityContext error codes into actionable advice.
    """
    if not raw_message:
        return ""

    match = re.search(r"data\s+([0-9a-fA-F]+)", raw_message)
    if match:
        code = match.group(1).lower()
        if code == "773":
            return (
                "AD Account Policy Restriction (data 773): 'User must change password at next logon' is ENABLED "
                "on this service account in Active Directory. Active Directory blocks non-interactive LDAP binds until this requirement is removed. "
                "Action Required: In Active Directory Users and Computers (ADUC) on the Domain Controller, "
                "open service account properties -> Account tab -> UNCHECK 'User must change password at next logon' "
                "and CHECK 'Password never expires'."
            )
        elif code == "52e":
            return (
                "Invalid Credentials or Format (data 52e): The username or password was rejected by the Domain Controller. "
                "Ensure the account name uses UPN format (e.g. ai.agent@nfc.co.om) or NetBIOS (NFC\\ai.agent), "
                "and verify the service account password."
            )
        elif code == "532":
            return "Password Expired (data 532): The password for this Active Directory service account has expired."
        elif code == "533":
            return "Account Disabled (data 533): The service account is disabled in Active Directory."
        elif code == "701":
            return "Account Expired (data 701): The service account has expired in Active Directory."
        elif code == "775":
            return "Account Locked Out (data 775): The service account has been locked out due to multiple failed logon attempts."
        elif code == "525":
            return "User Not Found (data 525): The specified user account does not exist in Active Directory."

    return raw_message


# -----------------------------------------------------------------------------
# Configuration Resolver
# -----------------------------------------------------------------------------

def get_effective_ad_config(override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Returns effective Active Directory settings by merging environment variables
    with database settings table and optional runtime overrides.
    """
    conn = db.get_db()
    db_settings = {}
    try:
        rows = conn.execute("SELECT key, value FROM settings WHERE key LIKE 'ad_%'").fetchall()
        for r in rows:
            db_settings[r["key"]] = r["value"]
    except Exception:
        pass
    finally:
        conn.close()

    def _val(key: str, env_val: Any, default_val: Any) -> Any:
        short_key = key.replace("ad_", "")
        if override:
            if key in override and override[key] is not None:
                return override[key]
            if short_key in override and override[short_key] is not None:
                return override[short_key]
        if key in db_settings and db_settings[key] != "":
            val = db_settings[key]
            if isinstance(default_val, bool):
                return str(val).lower() in ("true", "1", "yes")
            if isinstance(default_val, int):
                try:
                    return int(val)
                except ValueError:
                    return default_val
            return val
        return env_val if env_val is not None else default_val

    raw_bind = _val("ad_bind_dn", config.AD_BIND_DN, "")
    base_dn_val = _val("ad_base_dn", config.AD_BASE_DN, "DC=nationalfinance,DC=local")
    normalized_bind = normalize_bind_dn(raw_bind, base_dn_val)

    return {
        "enabled": _val("ad_enabled", config.AD_ENABLED, False),
        "server": _val("ad_server", config.AD_SERVER, "ldaps://127.0.0.1"),
        "port": _val("ad_port", config.AD_PORT, 636),
        "use_ssl": _val("ad_use_ssl", config.AD_USE_SSL, True),
        "use_starttls": _val("ad_use_starttls", config.AD_USE_STARTTLS, False),
        "verify_cert": _val("ad_verify_cert", config.AD_VERIFY_CERT, False),
        "ca_cert_path": _val("ad_ca_cert_path", config.AD_CA_CERT_PATH, ""),
        "bind_dn": normalized_bind,
        "raw_bind_dn": raw_bind,
        "password": _val("ad_password", config.AD_PASSWORD, ""),
        "base_dn": base_dn_val,
        "search_filter": _val("ad_search_filter", config.AD_SEARCH_FILTER, "(&(objectCategory=person)(objectClass=user))"),
        "page_size": _val("ad_page_size", config.AD_PAGE_SIZE, 500),
        "sync_interval_minutes": _val("ad_sync_interval_minutes", config.AD_SYNC_INTERVAL_MINUTES, 30),
        "p0_groups": config.AD_P0_GROUPS,
        "p1_groups": config.AD_P1_GROUPS,
    }


# -----------------------------------------------------------------------------
# Active Directory Diagnostic & Connection Testing
# -----------------------------------------------------------------------------

def test_ad_connection(custom_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Validates Active Directory connectivity, TLS handshake, service account authentication,
    and canary search latency. Safe and non-destructive.
    """
    if not LDAP3_AVAILABLE:
        return {
            "success": False,
            "error_code": "LDAP3_NOT_INSTALLED",
            "message": "Python 'ldap3' library is not installed in the runtime environment.",
            "latency_ms": 0,
        }

    cfg = get_effective_ad_config(custom_config)
    server_host = cfg["server"]
    # Strip protocol prefix if present (ldap:// or ldaps://)
    clean_host = re.sub(r"^ldaps?://", "", server_host).rstrip("/")

    tls_configuration = None
    if cfg["use_ssl"] or cfg["use_starttls"]:
        validate_mode = ssl.CERT_REQUIRED if cfg["verify_cert"] else ssl.CERT_NONE
        ca_file = cfg["ca_cert_path"] if cfg["ca_cert_path"] and os.path.exists(cfg["ca_cert_path"]) else None
        try:
            tls_configuration = Tls(validate=validate_mode, ca_certs_file=ca_file)
        except Exception as e:
            logger.warning(f"[AD_SYNC] TLS setup warning: {e}")

    t0 = time.perf_counter()

    try:
        server = Server(
            host=clean_host,
            port=cfg["port"],
            use_ssl=cfg["use_ssl"],
            tls=tls_configuration,
            get_info=ALL,
            connect_timeout=5,
        )

        conn = Connection(
            server,
            user=cfg["bind_dn"],
            password=cfg["password"],
            auto_bind=False,
            receive_timeout=10,
        )

        # Open socket and bind
        conn.open()
        if conn.closed:
            return {
                "success": False,
                "error_code": "SOCKET_OPEN_FAILED",
                "message": f"Could not establish TCP connection to Domain Controller {clean_host}:{cfg['port']}.",
                "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
            }

        if cfg["use_starttls"]:
            conn.start_tls()

        if not conn.bind():
            err_desc = conn.result.get("description") or "invalidCredentials"
            err_msg = conn.result.get("message") or ""
            full_raw = f"{err_desc} - {err_msg}".strip(" -")
            ad_explanation = explain_ad_error(full_raw)
            return {
                "success": False,
                "error_code": "BIND_FAILED",
                "message": f"Active Directory authentication failed for service account '{cfg['bind_dn']}': {ad_explanation if ad_explanation != full_raw else full_raw}",
                "ad_details": ad_explanation,
                "raw_result": conn.result,
                "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
            }

        # Canary Search (1 record) to verify Base DN and permissions
        canary_filter = cfg["search_filter"]
        conn.search(
            search_base=cfg["base_dn"],
            search_filter=canary_filter,
            search_scope=SUBTREE,
            attributes=["cn", "displayName", "sAMAccountName"],
            size_limit=1,
        )

        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        sample_name = ""
        if conn.entries:
            e = conn.entries[0]
            sample_name = str(getattr(e, "displayName", "") or getattr(e, "cn", "") or "Sample Account")

        conn.unbind()

        return {
            "success": True,
            "latency_ms": latency_ms,
            "server": server_host,
            "port": cfg["port"],
            "ssl": cfg["use_ssl"],
            "bind_user": cfg["bind_dn"],
            "base_dn": cfg["base_dn"],
            "sample_verified": bool(sample_name),
            "sample_account": sample_name,
            "message": f"Successfully authenticated with Active Directory in {latency_ms} ms. Ready for synchronization.",
        }

    except LDAPSocketOpenError as exc:
        return {
            "success": False,
            "error_code": "CONNECTION_TIMEOUT",
            "message": f"Network timeout connecting to Domain Controller at {clean_host}:{cfg['port']}. Verify firewall and port {cfg['port']}.",
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
            "details": str(exc),
        }
    except LDAPBindError as exc:
        ad_explanation = explain_ad_error(str(exc))
        return {
            "success": False,
            "error_code": "INVALID_CREDENTIALS",
            "message": f"Invalid service account credentials for '{cfg['bind_dn']}': {ad_explanation if ad_explanation != str(exc) else str(exc)}",
            "ad_details": ad_explanation,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
            "details": str(exc),
        }
    except Exception as exc:
        return {
            "success": False,
            "error_code": "UNEXPECTED_ERROR",
            "message": f"Active Directory connection error: {str(exc)}",
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }


# -----------------------------------------------------------------------------
# Active Directory Paged Synchronization Engine
# -----------------------------------------------------------------------------

def sync_active_directory(
    triggered_by: str = "system",
    custom_config: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Executes a paged LDAP synchronization from Active Directory into the local callers table.
    Safety guarantees:
    - Never wipes custom Arabic/English aliases.
    - Accurately parses userAccountControl (0x0002) for offboarding.
    - Synchronizes to users.csv for local cache durability.
    - Never hard deletes missing accounts (prevents catastrophic deletion from bad OUs).
    """
    if not LDAP3_AVAILABLE:
        raise RuntimeError("Python 'ldap3' package is required for Active Directory synchronization.")

    cfg = get_effective_ad_config(custom_config)
    clean_host = re.sub(r"^ldaps?://", "", cfg["server"]).rstrip("/")

    tls_configuration = None
    if cfg["use_ssl"] or cfg["use_starttls"]:
        validate_mode = ssl.CERT_REQUIRED if cfg["verify_cert"] else ssl.CERT_NONE
        ca_file = cfg["ca_cert_path"] if cfg["ca_cert_path"] and os.path.exists(cfg["ca_cert_path"]) else None
        try:
            tls_configuration = Tls(validate=validate_mode, ca_certs_file=ca_file)
        except Exception:
            pass

    server = Server(
        host=clean_host,
        port=cfg["port"],
        use_ssl=cfg["use_ssl"],
        tls=tls_configuration,
        get_info=ALL,
        connect_timeout=5,
    )

    conn = Connection(
        server,
        user=cfg["bind_dn"],
        password=cfg["password"],
        auto_bind=False,
        receive_timeout=15,
    )

    conn.open()
    if conn.closed:
        raise RuntimeError(f"Could not establish network connection to Active Directory ({clean_host}:{cfg['port']})")

    if cfg["use_starttls"]:
        conn.start_tls()

    if not conn.bind():
        err_desc = conn.result.get("description") or "invalidCredentials"
        err_msg = conn.result.get("message") or ""
        full_raw = f"{err_desc} - {err_msg}".strip(" -")
        ad_explanation = explain_ad_error(full_raw)
        raise RuntimeError(f"Failed to authenticate with Active Directory for '{cfg['bind_dn']}': {ad_explanation if ad_explanation != full_raw else full_raw}")

    search_attributes = [
        "sAMAccountName", "employeeID", "employeeNumber",
        "displayName", "givenName", "sn", "cn",
        "mail", "userPrincipalName",
        "telephoneNumber", "mobile", "ipPhone",
        "department", "title",
        "userAccountControl", "memberOf",
    ]

    page_size = cfg["page_size"]
    total_processed = 0
    added_count = 0
    updated_count = 0
    deactivated_count = 0
    now = int(time.time())

    db_conn = db.get_db()

    try:
        # Paged Search Iterator
        entry_generator = conn.extend.standard.paged_search(
            search_base=cfg["base_dn"],
            search_filter=cfg["search_filter"],
            search_scope=SUBTREE,
            attributes=search_attributes,
            paged_size=page_size,
            generator=True,
        )

        for entry_response in entry_generator:
            if entry_response.get("type") != "searchResEntry":
                continue

            raw_attributes = entry_response.get("attributes", {})
            total_processed += 1

            # 1. Resolve Employee ID
            emp_id = (
                raw_attributes.get("employeeID")
                or raw_attributes.get("employeeNumber")
                or raw_attributes.get("sAMAccountName")
                or ""
            )
            emp_id = str(emp_id).strip()
            if not emp_id:
                continue

            # 2. Resolve Name
            display_name = raw_attributes.get("displayName")
            if not display_name:
                given = raw_attributes.get("givenName") or ""
                surname = raw_attributes.get("sn") or ""
                display_name = f"{given} {surname}".strip()
            if not display_name:
                display_name = raw_attributes.get("cn") or emp_id
            name = str(display_name).strip()

            # 3. Resolve Email
            email = str(raw_attributes.get("mail") or raw_attributes.get("userPrincipalName") or "").strip()

            # 4. Resolve Phone (CLI)
            phone_raw = (
                raw_attributes.get("telephoneNumber")
                or raw_attributes.get("mobile")
                or raw_attributes.get("ipPhone")
                or ""
            )
            phone = str(phone_raw).strip()

            # 5. Resolve Department & Title
            department = str(raw_attributes.get("department") or "").strip()
            role = str(raw_attributes.get("title") or "Employee").strip()

            # 6. Resolve Active / Disabled Status via userAccountControl
            uac_val = raw_attributes.get("userAccountControl")
            uac_info = parse_user_account_control(uac_val)
            active_status = uac_info["active"]

            # 7. Resolve Priority Tier from memberOf Groups
            member_of = raw_attributes.get("memberOf") or []
            if isinstance(member_of, str):
                member_of = [member_of]

            member_of_str = " ".join(str(g).lower() for g in member_of)
            tier = "STANDARD"
            for g in cfg["p0_groups"]:
                if g.lower() in member_of_str:
                    tier = "P0_EXECUTIVE"
                    break

            if tier == "STANDARD":
                for g in cfg["p1_groups"]:
                    if g.lower() in member_of_str:
                        tier = "P1_VIP"
                        break

            vip = 1 if tier in ("P0_EXECUTIVE", "P1_VIP") else 0

            # 8. Check Existing Record in Local Database
            existing = db_conn.execute("SELECT id, aliases, active, tier FROM callers WHERE employee_id = ?", (emp_id,)).fetchone()

            if existing:
                # IMPORTANT: Preserve existing custom Arabic / English aliases!
                existing_aliases = existing["aliases"] or ""
                was_active = existing["active"]

                # If manual override elevated them to VIP, preserve it
                effective_tier = existing["tier"] if existing["tier"] in ("P0_EXECUTIVE", "P1_VIP") and tier == "STANDARD" else tier
                effective_vip = 1 if effective_tier in ("P0_EXECUTIVE", "P1_VIP") else 0

                db_conn.execute(
                    """
                    UPDATE callers
                    SET name=?, email=?, phone=?, department=?, role=?, vip=?, tier=?, active=?, source='ad_sync', updated_at=?
                    WHERE id=?
                    """,
                    (name, email, phone, department, role, effective_vip, effective_tier, active_status, now, existing["id"]),
                )
                updated_count += 1
                if was_active == 1 and active_status == 0:
                    deactivated_count += 1
            else:
                db_conn.execute(
                    """
                    INSERT INTO callers (
                        employee_id, name, aliases, email, phone,
                        department, role, vip, tier, active, source, updated_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (emp_id, name, "", email, phone, department, role, vip, tier, active_status, "ad_sync", now, now),
                )
                added_count += 1
                if active_status == 0:
                    deactivated_count += 1

        db_conn.commit()

        # Update Last Sync Stats in Settings Table
        sync_stats = {
            "total_scanned": total_processed,
            "added": added_count,
            "updated": updated_count,
            "deactivated": deactivated_count,
            "timestamp": now,
        }

        for k, v in [
            ("ad_last_sync_time", str(now)),
            ("ad_last_sync_status", "success"),
            ("ad_last_sync_stats", json.dumps(sync_stats)),
        ]:
            db_conn.execute(
                """
                INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
                """,
                (k, v, now),
            )
        db_conn.commit()

    except Exception as exc:
        db_conn.rollback()
        try:
            db_conn.execute(
                """
                INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
                """,
                ("ad_last_sync_status", "failed", now),
            )
            db_conn.commit()
        except Exception:
            pass
        logger.error(f"[AD_SYNC] Sync failure: {exc}", exc_info=True)
        raise
    finally:
        db_conn.close()
        try:
            conn.unbind()
        except Exception:
            pass

    # Synchronize to users.csv file and flush in-memory lookup cache
    db.sync_callers_to_csv()
    clear_cache()

    logger.info(
        f"[AD_SYNC] Completed sync by {triggered_by}: "
        f"processed={total_processed}, added={added_count}, updated={updated_count}, deactivated={deactivated_count}"
    )

    return {
        "success": True,
        "total_scanned": total_processed,
        "added": added_count,
        "updated": updated_count,
        "deactivated": deactivated_count,
        "timestamp": now,
        "message": f"Successfully synchronized {total_processed} employee records from Active Directory.",
    }


# -----------------------------------------------------------------------------
# Active Directory Operational Telemetry
# -----------------------------------------------------------------------------

def get_ad_telemetry() -> Dict[str, Any]:
    """Returns real-time operational state and last sync diagnostics for the dashboard."""
    cfg = get_effective_ad_config()

    conn = db.get_db()
    last_sync_time_str = "Never"
    last_sync_status = "never"
    last_sync_stats = None
    last_sync_raw = 0

    try:
        row_time = conn.execute("SELECT value FROM settings WHERE key = 'ad_last_sync_time'").fetchone()
        if row_time and row_time["value"]:
            last_sync_raw = int(row_time["value"])
            diff_mins = max(0, int((time.time() - last_sync_raw) // 60))
            if diff_mins == 0:
                last_sync_time_str = "Just now"
            elif diff_mins < 60:
                last_sync_time_str = f"{diff_mins}m ago"
            else:
                last_sync_time_str = f"{diff_mins // 60}h {diff_mins % 60}m ago"

        row_status = conn.execute("SELECT value FROM settings WHERE key = 'ad_last_sync_status'").fetchone()
        if row_status and row_status["value"]:
            last_sync_status = row_status["value"]

        row_stats = conn.execute("SELECT value FROM settings WHERE key = 'ad_last_sync_stats'").fetchone()
        if row_stats and row_stats["value"]:
            try:
                last_sync_stats = json.loads(row_stats["value"])
            except Exception:
                pass
    except Exception:
        pass
    finally:
        conn.close()

    return {
        "ldap3_installed": LDAP3_AVAILABLE,
        "enabled": cfg["enabled"],
        "server": cfg["server"],
        "port": cfg["port"],
        "ssl": cfg["use_ssl"],
        "base_dn": cfg["base_dn"],
        "bind_user": cfg["bind_dn"],
        "sync_interval_mins": cfg["sync_interval_minutes"],
        "last_sync_time_human": last_sync_time_str,
        "last_sync_timestamp": last_sync_raw,
        "last_sync_status": last_sync_status,
        "last_sync_stats": last_sync_stats,
    }


# -----------------------------------------------------------------------------
# Background Sync Worker Thread
# -----------------------------------------------------------------------------

_worker_thread = None
_worker_running = False


def _ad_sync_worker_loop():
    logger.info("[AD_SYNC] Active Directory background sync worker thread started.")
    while _worker_running:
        try:
            cfg = get_effective_ad_config()
            if cfg["enabled"] and LDAP3_AVAILABLE:
                telemetry = get_ad_telemetry()
                last_ts = telemetry["last_sync_timestamp"]
                interval_secs = cfg["sync_interval_minutes"] * 60

                if time.time() - last_ts >= interval_secs:
                    logger.info("[AD_SYNC] Triggering scheduled Active Directory sync...")
                    try:
                        sync_active_directory(triggered_by="scheduler")
                    except Exception as err:
                        logger.error(f"[AD_SYNC] Scheduled sync error: {err}")
        except Exception as exc:
            logger.error(f"[AD_SYNC] Worker loop error: {exc}")

        # Sleep in short increments to allow graceful shutdown
        for _ in range(60):
            if not _worker_running:
                break
            time.sleep(1)


def start_ad_sync_worker():
    """Starts the background Active Directory sync worker thread if not already running."""
    global _worker_thread, _worker_running
    if _worker_running:
        return

    _worker_running = True
    _worker_thread = threading.Thread(target=_ad_sync_worker_loop, daemon=True, name="ADSyncWorker")
    _worker_thread.start()


def stop_ad_sync_worker():
    """Stops the background worker thread."""
    global _worker_running
    _worker_running = False
