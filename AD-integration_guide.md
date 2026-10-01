# Active Directory (AD / LDAP) Enterprise Integration Guide
### National Finance — AI IT Support Voice Agent ("Arif")

This document provides a comprehensive, step-by-step guide for integrating Microsoft Active Directory (AD / LDAP) with the National Finance Voice AI IT Support Agent.

---

## 1. Overview & Architecture

Integrating Active Directory with the Voice AI agent enables:
1. **Zero-Friction Caller Identification:** When employees dial from their registered office extension or corporate mobile (`telephoneNumber` / `mobile`), the AI instantly recognizes them by name.
2. **Instant Identity Verification:** Callers can authenticate verbally or via telephone keypad using their corporate Employee ID (`employeeID` or `sAMAccountName`).
3. **Executive VIP Concierge Fast-Track:** Callers belonging to designated executive groups (e.g., `C-Suite`, `Executives`, `CEO`, `CFO`) bypass standard diagnostic troubleshooting and are immediately transferred to Senior Human IT Support (**Cisco Webex extension `8002`**).
4. **Automated Lifecycle & Offboarding:** Accounts marked as disabled in Active Directory (`userAccountControl` bitmask `0x0002`) are immediately rejected by the AI, preventing unauthorized IT service desk requests.

### Data Flow Diagram

```
+-----------------------------------+
|  Microsoft Active Directory (AD)  |
|  (Domain Controllers / LDAPS:636) |
+-----------------+-----------------+
                  |
                  | Encrypted LDAPS (TCP 636)
                  | Read-Only Paged Query (500/batch)
                  v
+-----------------+-----------------+
|   VM 1: Voice & Edge Server       |
|   (10.1.120.165)                  |
|   - ad_sync.py Sync Engine        |
|   - Realtime Voice Bridge (Arif)  |
+-----------------+-----------------+
                  |
                  | Write Callers & Profiles (TCP 5432)
                  v
+-----------------+-----------------+
|   VM 2: Core & Database Server    |
|   (10.1.120.166)                  |
|   - PostgreSQL 16 `callers` table |
|   - Frappe Helpdesk Customer Sync |
+-----------------------------------+
```

---

## 2. Phase 1: What the Active Directory Admin Needs to Do

The Active Directory Domain Administrator must perform the following 5 tasks:

### Task 1.1: Create a Dedicated Read-Only Service Account
The AI agent only requires **standard read permissions** to query user objects. It does **not** require Domain Admin or privileged group membership.

* **Account Name:** `svc-ai-voice` (or `svc-ai-agent`)
* **User Logon Name (sAMAccountName):** `svc-ai-voice`
* **User Principal Name (UPN):** `svc-ai-voice@nationalfinance.local`
* **Account Type:** Standard Domain User
* **Password Policy:** Strong password, set to **Never Expire** (or managed per corporate policy).

> **PowerShell Command (Run as Domain Admin on DC):**
> ```powershell
> New-ADUser -Name "svc-ai-voice" `
>            -SamAccountName "svc-ai-voice" `
>            -UserPrincipalName "svc-ai-voice@nationalfinance.local" `
>            -DisplayName "Service Account - AI IT Voice Agent" `
>            -Description "Read-only LDAP directory reader for National Finance AI Voice Support Agent" `
>            -Path "OU=ServiceAccounts,DC=nationalfinance,DC=local" `
>            -AccountPassword (ConvertTo-SecureString "SecureVoicePass2026!" -AsPlainText -Force) `
>            -Enabled $true `
>            -PasswordNeverExpires $true
> ```

---

### Task 1.2: Ensure Required User Attributes Are Populated
The AI sync engine extracts the following standard LDAP attributes. Ensure these attributes are maintained in user profiles:

| AD LDAP Attribute | Example Value | Usage in AI Voice Agent |
| :--- | :--- | :--- |
| `sAMAccountName` | `j.smith` | User username / login |
| `employeeID` or `employeeNumber` | `1002` | **Primary identification number** spoken by caller or entered via phone keypad |
| `displayName` | `John Smith` | Spoken name for personalized greeting ("Hello Mr. John...") |
| `telephoneNumber` | `+96824123456` or `8002` | Desk phone / extension for instant caller ID matching |
| `mobile` | `+96891234567` | Mobile number for instant caller ID matching |
| `mail` | `j.smith@nationalfinance.com` | Email address used when generating Helpdesk tickets |
| `department` | `Finance & Accounts` | Stored in ticket metadata and displayed on dashboard |
| `title` | `Senior Financial Analyst` | Displayed on agent dashboard |
| `userAccountControl` | `512` (Normal) / `514` (Disabled) | Evaluates active vs. offboarded status |
| `memberOf` | `CN=Executives,OU=Groups...` | Determines priority tier (`P0_EXECUTIVE` vs `STANDARD`) |

---

### Task 1.3: Configure VIP / Executive Security Groups
The AI agent categorizes callers into tiers based on their Active Directory group membership:

1. **Tier P0 (Executive / Concierge):**
   * Groups: `C-Suite`, `Executives`, `CEO`, `CFO`, `Board-Members`
   * *Behavior:* When verified or identified by phone, Arif immediately says: *"Welcome Mr. [Name]. I am connecting you directly to our Senior Executive Support Desk right now,"* and transfers the call to **Webex extension `8002`**.
2. **Tier P1 (Priority):**
   * Groups: `Directors`, `Heads-of-Department`, `VIP`
   * *Behavior:* Highlighted on dashboard and prioritized in Helpdesk SLA routing.
3. **Standard Tier:**
   * All other employees receive self-service diagnostic guidance, playbook troubleshooting, and automated ticket logging.

---

### Task 1.4: Network & Firewall Allowance
The network team must allow traffic from **VM 1 (`10.1.120.165`)** to the Domain Controller(s):

* **Source IP:** `10.1.120.165` (Voice Server)
* **Destination IP:** Domain Controller IP(s) (e.g., `10.1.x.x`)
* **Port:** **TCP Port 636** (LDAPS — Secure LDAP over SSL) *(Strongly Recommended)*
* **Alternative Port:** **TCP Port 389** (LDAP with StartTLS)

---

### Task 1.5: Handover Checklist from AD Admin
The Active Directory Administrator must hand over the following details to the Voice AI team:

```text
Domain Controller Host/IP:   [e.g. 10.1.10.20 or dc01.nationalfinance.local]
Port:                        [636 for LDAPS / 389 for LDAP]
Protocol:                    [LDAPS / StartTLS]
Base DN:                     [e.g. DC=nationalfinance,DC=local or OU=Users,DC=nationalfinance,DC=local]
Service Account Bind DN:     [e.g. CN=svc-ai-voice,OU=ServiceAccounts,DC=nationalfinance,DC=local or svc-ai-voice@nationalfinance.local]
Service Account Password:    [****************]
Executive Security Groups:   [e.g. C-Suite, Executives, CEO, CFO]
Root CA Certificate:         [Optional: Required only if strict SSL verification is enabled]
```

---

## 3. Phase 2: What You (The System Engineer) Need to Do on VM 1

Once the AD administrator provides the details, follow these steps on **VM 1 (`10.1.120.165`)**:

### Step 2.1: Update Environment Configuration (`.env`)
SSH into VM 1 and edit `/opt/ai-support-agent/.env`:

```bash
sudo nano /opt/ai-support-agent/.env
```

Locate the **Active Directory** section and update it with the real details:

```ini
# =============================================================================
# ACTIVE DIRECTORY (AD / LDAP) ENTERPRISE CONNECTOR
# =============================================================================
AD_ENABLED="true"

# Domain Controller URL and Port
AD_SERVER="ldaps://10.1.10.20"           # Replace with real Domain Controller IP or FQDN
AD_PORT=636                             # 636 for LDAPS, 389 for LDAP/StartTLS
AD_USE_SSL="true"                       # true for LDAPS (port 636)
AD_USE_STARTTLS="false"                 # true if using port 389 with StartTLS

# Certificate Validation
AD_VERIFY_CERT="false"                  # Set "true" if enterprise Root CA is installed
AD_CA_CERT_PATH=""                      # Path to CA cert if AD_VERIFY_CERT="true" (e.g. /etc/ssl/certs/ad-ca.pem)

# Service Account Credentials
AD_BIND_DN="CN=svc-ai-voice,OU=ServiceAccounts,DC=nationalfinance,DC=local"
AD_PASSWORD="YourSecurePasswordHere"

# Base Search DN and Filter
AD_BASE_DN="DC=nationalfinance,DC=local"
AD_SEARCH_FILTER="(&(objectCategory=person)(objectClass=user))"

# Performance & Sizing
AD_PAGE_SIZE=500                        # Queries in batches of 500 to handle >10,000 users safely
AD_SYNC_INTERVAL_MINUTES=30             # Automatic background sync frequency (minutes)

# VIP / Executive Group Matching (Comma-separated)
AD_P0_GROUPS="C-Suite,Executives,CEO,CFO"
AD_P1_GROUPS="Directors,Heads,VIP"
```

Save the file (`Ctrl+O`, `Enter`, `Ctrl+X`).

---

### Step 2.2: Verify Network Reachability from VM 1
Before testing credentials, confirm that VM 1 can establish a TCP connection to the Domain Controller on port 636:

```bash
/opt/ai-support-agent/venv/bin/python -c "
import socket
target = '10.1.10.20' # Replace with your Domain Controller IP
port = 636
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(3.0)
res = s.connect_ex((target, port))
s.close()
print('DC Port 636 Reachability:', 'SUCCESS (OPEN)' if res == 0 else f'BLOCKED (Code {res})')
"
```

* If it returns `SUCCESS (OPEN)`, proceed to Step 2.3.
* If it returns `BLOCKED (Code 110)` (Timeout), verify the firewall route with the network admin.

---

### Step 2.3: Run the In-Flight Diagnostic Test
Run the built-in diagnostic tool to test TLS negotiation, service account credentials, and sample query latency:

```bash
PYTHONPATH=/opt/ai-support-agent /opt/ai-support-agent/venv/bin/python -c "
import json
from app.ad_sync import test_ad_connection
result = test_ad_connection()
print(json.dumps(result, indent=2))
"
```

#### Expected Successful Output:
```json
{
  "success": true,
  "latency_ms": 14.2,
  "server": "ldaps://10.1.10.20",
  "port": 636,
  "ssl": true,
  "bind_user": "CN=svc-ai-voice,OU=ServiceAccounts,DC=nationalfinance,DC=local",
  "base_dn": "DC=nationalfinance,DC=local",
  "sample_verified": true,
  "sample_account": "John Smith",
  "message": "Successfully authenticated with Active Directory in 14.2 ms. Ready for synchronization."
}
```

---

### Step 2.4: Execute the First Full Synchronization
Run a manual sync to populate the PostgreSQL database (`callers` table) and local cache with all Active Directory accounts:

```bash
PYTHONPATH=/opt/ai-support-agent /opt/ai-support-agent/venv/bin/python -c "
import json
from app.ad_sync import sync_active_directory
summary = sync_active_directory(triggered_by='initial_deployment')
print(json.dumps(summary, indent=2))
"
```

#### Expected Output:
```json
{
  "success": true,
  "total_processed": 1420,
  "added": 1420,
  "updated": 0,
  "deactivated": 12,
  "duration_seconds": 3.4,
  "message": "Synchronized 1420 users from Active Directory (1420 added, 0 updated, 12 deactivated)."
}
```

---

### Step 2.5: Restart Services & Enable Periodic Auto-Sync
Restart the voice bridge and telemetry dashboard so they load the active directory configuration:

```bash
sudo systemctl restart ai-support-bridge
sudo systemctl restart ai-dashboard
```

Verify service status:
```bash
sudo systemctl status ai-support-bridge ai-dashboard --no-pager
```

*The dashboard background worker will now automatically sync changes (new hires, offboarded users, phone updates) every 30 minutes.*

---

## 4. Phase 3: Verification & Live Call Testing

### Verification 1: Inspect in Web Dashboard
1. Open your browser and navigate to: `http://10.1.120.165`
2. Log in with:
   * **Username:** `admin`
   * **Password:** `C@llnFc$2026`
3. Click **User Directory / Callers**:
   * Verify corporate employee names, departments, phone numbers, and Employee IDs.
   * Verify that executives have the **P0_EXECUTIVE / VIP** badge.
4. Click **AD Sync Status**:
   * View the last sync timestamp, latency, and sync count.
   * You can also click **"Sync Now"** at any time to trigger an on-demand refresh.

---

### Verification 2: Standard Employee Call Test
1. An employee calls `+96821130485`.
2. When Arif asks for Employee ID, the caller speaks their 4-digit ID (e.g. *"1002"*):
   * Arif responds: *"Thank you Mr. John. How can I assist you today?"*
   * *Fallback:* If audio is noisy, caller enters `1002#` on their telephone keypad. Arif verifies immediately.

---

### Verification 3: VIP Executive Escalation Test
1. An executive listed in `AD_P0_GROUPS` (or calling from the CEO/CFO's registered phone number) dials `+96821130485`.
2. Arif immediately recognizes them:
   * *"Welcome Mr. [Name]. Connecting you directly to our Senior Executive Support Desk right now."*
3. Asterisk transfers the call immediately to **Webex extension `8002`**.
4. The Helpdesk automatically logs a P0 Executive ticket.

---

## 5. Troubleshooting & FAQ

### Issue 1: `SOCKET_OPEN_FAILED` or `CONNECTION_TIMEOUT`
* **Cause:** Firewall or security group is dropping packets between `10.1.120.165` and Domain Controller port 636.
* **Fix:** Ask the network security team to allow outbound traffic from `10.1.120.165` to DC IP on TCP port `636`. Test with:
  ```bash
  nc -zv -w 3 10.1.10.20 636
  ```

### Issue 2: `BIND_FAILED` / `INVALID_CREDENTIALS`
* **Cause:** Incorrect username format or wrong password.
* **Fix:**
  - Verify if user account is locked in AD.
  - Try using User Principal Name (UPN) format: `svc-ai-voice@nationalfinance.local` instead of Distinguished Name (DN).

### Issue 3: `SSL_CERTIFICATE_VERIFY_FAILED`
* **Cause:** The Domain Controller's LDAPS certificate is issued by an internal corporate CA that is not trusted by Ubuntu's default root certificate store.
* **Fix:** In `/opt/ai-support-agent/.env`, set:
  ```ini
  AD_VERIFY_CERT="false"
  ```
  *(This keeps all traffic encrypted over TLS while bypassing public CA chain verification).*

### Issue 4: Employee ID Not Found
* **Cause:** Some organizations store the employee badge number in `employeeNumber` instead of `employeeID`, or use `sAMAccountName`.
* **Fix:** The sync engine automatically checks `employeeID`, `employeeNumber`, and `sAMAccountName` in order. Ensure at least one of these fields contains the caller's ID in Active Directory.
