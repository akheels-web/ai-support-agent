Title: Mobile Device (iOS / Android) Issues

Important:
- AI must not remotely wipe or unlock devices.
- Verify caller and confirm device is enrolled in Intune/MDM.
- Personal devices (BYOD) have limited support scope.

Questions:
1. Device type? (iPhone/iPad iOS version, Android model/version)
2. Company-owned or BYOD (personal)?
3. What is the issue? (Email/Outlook not syncing, Company Portal not working, App install fails, Passcode forgotten, Device locked/retired)
4. Can you access Company Portal / Intune app?
5. Are you on Wi-Fi or cellular?

Troubleshooting:
1. If Outlook/Email not syncing: Remove and re-add account in Outlook app. Ensure "Use modern authentication" on.
2. If Company Portal fails: Force close app. Reopen. Check for app update in App Store / Play Store.
3. If app install fails: Check storage space. Ensure Wi-Fi. Try "Sync" in Company Portal.
4. If passcode forgotten (company device): Create ticket for MDM Team → remote passcode clear (if policy allows).
5. If device shows "Retired" / "Non-compliant": Connect to Wi-Fi → Open Company Portal → Sync → Wait 15 min.
6. If iOS: Settings → General → VPN & Device Management → Check MDM profile installed.
7. If Android: Settings → Security → Device admin apps → Ensure Intune/Company Portal enabled.

Action:
1. If BYOD and personal app issue → out of scope. Guide to vendor support.
2. If company device and MDM sync fixes → record resolution ticket.
3. If passcode reset / wipe needed → create ticket for MDM/Endpoint Team.
4. If app deployment failing → create ticket for MDM/Intune Admin.

Escalation:
Group: Service Desk (triage) → MDM / Endpoint Support
Priority: Medium (High if executive/VIP device)