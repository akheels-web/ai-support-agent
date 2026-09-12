Title: Windows Update Issues / Stuck Updates

Important:
- AI must not force updates via command line remotely.
- Verify caller and check if update is mandatory (security).
- Some updates require multiple restarts.

Questions:
1. What is the issue? (Stuck at %, fails with error code, keeps restarting, "pending restart" loop)
2. Any error code? (e.g., 0x80070002, 0x800f0922, 0x8024402f)
3. Is this a Feature Update (23H2, 24H2) or Quality Update?
4. Are you on VPN or office network?
5. How long has it been stuck?

Troubleshooting:
1. Wait: Feature updates can take 60-120 minutes. Quality updates 15-30 min.
2. If stuck >2 hours: Restart once. Windows will resume.
3. Run Windows Update Troubleshooter: Settings → System → Troubleshoot → Other → Windows Update.
4. If error 0x800f0922 (VPN/firewall): Disconnect VPN. Ensure port 443/80 open to Microsoft.
5. If error 0x80070002 (missing files): Run cmd as admin → DISM /Online /Cleanup-Image /RestoreHealth → sfc /scannow.
6. If "Pending restart" loop: Restart fully (Start → Power → Restart, not Shutdown). Check BitLocker not prompting.
7. If on metered connection: Set connection as unmetered temporarily (Settings → Network → Properties).

Action:
1. If troubleshooting resolves → record resolution ticket.
2. If repeated failures → create ticket for Endpoint Support (WSUS/Intune policy).
3. If BitLocker recovery needed → create ticket for Security/Endpoint (recovery key).
4. If mandatory security update not applying → escalate to Patch Management Team.

Escalation:
Group: Service Desk (triage) → Endpoint Support / Patch Management
Priority: Low (High if critical security update failing)