Title: User Locked Out of Computer

Symptoms:
- User cannot log in to company computer.
- User says account is locked.
- User says password is not accepted.
- User sees message: account locked, account disabled, too many failed attempts, or contact administrator.

Important:
- Do not tell the user to restart the computer as the main solution.
- Restarting does not unlock a locked domain account.
- First verify the user.
- Then identify if it is local computer lockout, domain account lockout, password expired, or MFA issue.

Questions to ask:
1. Are you using a company domain account?
2. What exact message do you see on the screen?
3. Are you connected to office network or VPN?
4. Did you recently change your password?
5. Can you login to webmail or Microsoft 365 from another device?

Troubleshooting:
1. If account is locked, create high priority ticket for Service Desk to unlock account.
2. If password expired, guide user to password reset portal if available.
3. If user is remote and cached password is old, ask user to connect to VPN from login screen if supported.
4. If MFA issue, create ticket for identity support.
5. If computer says local profile issue, create ticket for endpoint support.

Escalation:
Group: Service Desk
Priority: High
Ticket title: Account locked or login issue
