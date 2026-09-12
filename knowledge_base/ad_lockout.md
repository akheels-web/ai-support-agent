Title: Active Directory Account Lockout

Important:
- AI must not unlock accounts directly.
- Verify caller identity (Full Name + Employee ID) before any action.
- Distinguish between local computer lockout and AD account lockout.

Questions:
1. What exact message do you see? ("The referenced account is currently locked out", "Account locked", "Too many failed attempts")
2. Where are you trying to log in? (Office PC, VPN, OWA, Teams, Mobile)
3. Have you recently changed your password?
4. Are you using a password manager or saved credentials in browser?
5. Any recent failed login attempts you recall?

Troubleshooting:
1. If on VPN: Disconnect VPN. Wait 15 minutes (lockout duration). Try again.
2. If password recently changed: Update saved credentials everywhere (browser, phone, tablet, password manager, mapped drives).
3. Clear cached credentials: Control Panel → Credential Manager → Windows Credentials → Remove all entries for company domain.
4. Check for old devices: Phone/tablet with old password syncing email → update or remove account.
5. If OWA (outlook.office.com) works but PC/VPN fails → cached credential issue on device.
6. If OWA also fails → AD account locked. Create unlock ticket.

Action:
1. If verified and OWA works → guide credential cleanup. Record resolution if fixed.
2. If verified and OWA fails → create ticket for Service Desk (AD Unlock). Priority High.
3. If not verified → create unverified caller ticket for Service Desk.
4. If recurring lockouts (>2 in 24h) → escalate to Identity Team for root cause (service account, app, script).

Escalation:
Group: Service Desk (AD Unlock) → Identity Team (recurring)
Priority: High (Critical if executive/VIP)