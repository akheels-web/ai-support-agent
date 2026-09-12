Title: File Share / Network Drive Access Issues

Important:
- AI must not modify permissions or share paths.
- Verify caller and confirm they should have access.
- Distinguish between permission, connectivity, and mapping issues.

Questions:
1. Which drive/share? (Drive letter: S:, T:, \\server\share, SharePoint, OneDrive)
2. What is the error? ("Access denied", "Network path not found", "The handle is invalid", empty folder)
3. Are you on VPN or office network?
4. Can you access other shares?
5. Have you recently changed password?

Troubleshooting:
1. If on VPN: Ensure VPN connected. Disconnect/Reconnect. Try accessing by IP (\\10.x.x.x\share).
2. If "Access denied": Confirm with manager if access should exist. Create ticket for Access Review.
3. If drive missing after reboot: Remap manually (Win+E → This PC → Map Network Drive). Check "Reconnect at sign-in".
4. If SharePoint/OneDrive: Open in browser (sharepoint.com). If works there, sync client issue → reset OneDrive (Win+R → %localappdata%\Microsoft\OneDrive\onedrive.exe /reset).
5. If "The handle is invalid": Restart PC. Run cmd as admin → net use * /delete → remap.
6. If password recently changed: Lock workstation (Win+L) → unlock with new password. Remap drives.

Action:
1. If permission issue → create ticket for Identity/Access Team (Access Review).
2. If connectivity (VPN, network) → create ticket for Network Team.
3. If sync/client issue (OneDrive) → create ticket for Endpoint Support.
4. If verified and resolved → record resolution ticket.

Escalation:
Group: Service Desk (triage) → Identity & Access / Network / Endpoint Support
Priority: Medium (High if finance/HR share inaccessible)