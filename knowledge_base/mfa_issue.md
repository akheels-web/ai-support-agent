Title: MFA / Authenticator App Issues

Important:
- AI must not bypass MFA or provide authentication codes.
- AI must verify caller identity before proceeding.
- If authenticator app is lost/reset, escalate to Identity Team.

Questions:
1. Which MFA method are you using? (Microsoft Authenticator, Google Authenticator, SMS, Hardware Token)
2. Are you getting an error message? If so, what does it say?
3. Have you recently changed phones or reinstalled the authenticator app?
4. Is the device time synchronized correctly?
5. Are you able to approve the push notification?

Troubleshooting:
1. Verify device date/time is set to automatic (time drift causes TOTP failures).
2. If using Microsoft Authenticator: Open app → check for pending approval → approve.
3. If using Google Authenticator / TOTP: Ensure codes are 6 digits and entered within 30-second window.
4. If SMS code not received: Wait 2 minutes, request resend. Check signal/spam folder.
5. If hardware token (YubiKey): Ensure it is inserted/tapped correctly.
6. If "Number matching" prompt appears: Enter the 2-digit number shown on sign-in screen.

Action:
1. If verified and MFA method works → no ticket needed.
2. If verified but MFA device lost/reset → create ticket for Identity Team (MFA Reset).
3. If not verified → create unverified caller ticket for Service Desk.

Escalation:
Group: Identity & Access Management
Priority: High