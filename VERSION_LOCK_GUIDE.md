# Enterprise Open-Source Version Lock & Upgrade Prevention Guide

This document outlines the strict immutability controls implemented across **VM 1 (Voice & Edge)** and **VM 2 (Core & Data)** to guarantee that **no open-source component, container image, system package, or application dependency can be updated or overwritten**.

---

## 1. Summary of Implemented Controls

| Component | Target Server | Control Implemented | Status |
| :--- | :--- | :--- | :--- |
| **Frappe Helpdesk Docker** | VM 2 (`10.1.120.166`) | Pinned to immutable `sha256` digest in `/opt/frappe-helpdesk/docker-compose.yml` | **LOCKED** |
| **MariaDB Docker** | VM 2 (`10.1.120.166`) | Pinned to immutable `sha256` digest | **LOCKED** |
| **Redis Docker** | VM 2 (`10.1.120.166`) | Pinned to immutable `sha256` digest | **LOCKED** |
| **Frappe Bench Auto-Update** | VM 2 (`10.1.120.166`) | Disabled `auto_update` & `disable_auto_update: true` in `common_site_config.json` | **DISABLED** |
| **PostgreSQL 16** | VM 2 (`10.1.120.166`) | Locked via `apt-mark hold` (`postgresql-16`, `postgresql-client-16`) | **HELD** |
| **Docker Engine & Runtimes** | VM 2 (`10.1.120.166`) | Locked via `apt-mark hold` (`docker-ce`, `containerd.io`, compose plugins) | **HELD** |
| **Ubuntu Auto-Upgrades** | VM 2 (`10.1.120.166`) | `unattended-upgrades` service stopped, disabled & masked to `/dev/null` | **DISABLED & MASKED** |
| **Asterisk 20.21.0** | VM 1 (`10.1.120.165`) | Source-built binary in `/usr/sbin/asterisk`; protected against repo upgrades | **IMMUTABLE** |
| **Nginx Web Server** | VM 1 (`10.1.120.165`) | Locked via `apt-mark hold` (`nginx`, `nginx-core`, all modules) | **HELD** |
| **Python 3.10 Runtime** | VM 1 (`10.1.120.165`) | Locked via `apt-mark hold` (`python3`, `python3.10`, `python3-pip`, dev libraries) | **HELD** |
| **Python Application Venv** | VM 1 (`10.1.120.165`) | Pinned with exact `==` versions in `requirements.lock` and `requirements.txt` | **LOCKED** |
| **Ubuntu Auto-Upgrades** | VM 1 (`10.1.120.165`) | `unattended-upgrades` service stopped, disabled & masked to `/dev/null` | **DISABLED & MASKED** |

---

## 2. VM 2: Core & Data Platform Details

### A. Docker Compose Digest Pinning
File: `/opt/frappe-helpdesk/docker-compose.yml`

```yaml
version: "3.7"
services:
  mariadb:
    image: mariadb:10.8@sha256:456709ab146585d6189da05669b84384518baecd83670c9e5221f8c20a47cf1e
    ...
  redis:
    image: redis:alpine@sha256:3811787313eba226a2ef38658c6ccb91cd5e110edc89c37767de373120a0e5a0
  frappe:
    image: frappe/bench@sha256:a99c611f8f89bd04437b3cbd4ed0b11db8a0aa5b60fade4f29d02090f330e4af
    ...
```
*Why this matters*: Even if someone runs `docker compose pull`, Docker will strictly verify the hash and refuse to fetch newer images.

### B. Frappe Bench Auto-Update Configuration
File: `/home/frappe/frappe-bench/sites/common_site_config.json`
```json
{
  "auto_update": false,
  "disable_auto_update": true,
  "rebase_on_pull": false
}
```

### C. OS Package Holds (`apt-mark showhold`)
The following packages cannot be upgraded by `apt upgrade` or any automated script:
- `postgresql-16`
- `postgresql-client-16`
- `postgresql-client-common`
- `postgresql-common`
- `containerd.io`
- `docker-ce`
- `docker-ce-cli`
- `docker-buildx-plugin`
- `docker-compose-plugin`
- `docker-model-plugin`

### D. Ubuntu Background Upgrades Disabled
File: `/etc/apt/apt.conf.d/20auto-upgrades`
```
APT::Periodic::Update-Package-Lists "0";
APT::Periodic::Download-Upgradeable-Packages "0";
APT::Periodic::AutocleanInterval "0";
APT::Periodic::Unattended-Upgrade "0";
```
Service `unattended-upgrades.service` is masked:
```bash
Created symlink /etc/systemd/system/unattended-upgrades.service → /dev/null
```

---

## 3. VM 1: Voice & Edge Platform Details

### A. Nginx & Python Package Holds (`apt-mark showhold`)
The following core packages cannot be upgraded:
- `nginx`, `nginx-common`, `nginx-core`
- `libnginx-mod-*` (all 20 modules)
- `python3`, `python3.10`, `python3.10-dev`, `python3.10-minimal`, `python3.10-venv`
- `libpython3.10`, `libpython3.10-dev`, `libpython3.10-stdlib`
- `python3-pip`

### B. Application Dependencies Locked
File: `/opt/ai-support-agent/requirements.lock` & `requirements.txt`
All packages in the virtual environment are pinned to exact versions:
- `fastapi==0.142.1`
- `uvicorn==0.54.0`
- `websockets==16.1.1`
- `psycopg==3.3.6`
- `ldap3==2.9.1`
- `jinja2==3.1.6`
- `pydantic==2.13.5`
- `requests==2.34.2`

### C. Ubuntu Background Upgrades Disabled
File: `/etc/apt/apt.conf.d/20auto-upgrades`
```
APT::Periodic::Update-Package-Lists "0";
APT::Periodic::Download-Upgradeable-Packages "0";
APT::Periodic::AutocleanInterval "0";
APT::Periodic::Unattended-Upgrade "0";
```
Service `unattended-upgrades.service` is masked:
```bash
Created symlink /etc/systemd/system/unattended-upgrades.service → /dev/null
```

---

## 4. Verification Commands

To verify that locks remain active at any time:

```bash
# On either VM:
apt-mark showhold
systemctl is-active unattended-upgrades  # Output: inactive
systemctl is-enabled unattended-upgrades # Output: masked

# On VM 2:
cat /opt/frappe-helpdesk/docker-compose.yml | grep -E "image:"
```
