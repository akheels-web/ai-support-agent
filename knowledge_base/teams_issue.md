Title: Teams Audio / Video / Meeting Issues

Important:
- AI must not join calls or test audio remotely.
- Verify caller before creating ticket.
- Most issues are client-side (device, network, permissions).

Questions:
1. What is the issue? (No audio, no video, screen share not working, cannot join meeting, echo/feedback)
2. Are you using Teams desktop app, web app, or mobile?
3. Are you on VPN, office Wi-Fi, or home internet?
4. Using headset, built-in mic/speakers, or external devices?
5. Does the issue happen in all meetings or specific ones?

Troubleshooting:
1. Check device permissions: Settings → Privacy → Microphone/Camera → Allow Teams.
2. In Teams: Settings → Devices → Select correct speaker/microphone/camera. Make a test call.
3. If echo/feedback: Use headset. Lower speaker volume. Mute when not speaking.
4. If screen share fails: Ensure Teams has Screen Recording permission (macOS: System Settings → Privacy).
5. If cannot join meeting: Try "Join on web instead" link. Clear Teams cache (%appdata%\Microsoft\Teams → delete Cache, Blob_storage, databases, GPUCache, IndexedDB, Local Storage, tmp).
6. If on VPN: Disconnect VPN for Teams media (split tunneling). Teams media should go direct.
7. Update Teams: Click ... → Check for updates. Restart after update.

Action:
1. If test call works but meetings fail → network/VPN issue → create ticket for Network Team.
2. If device permissions correct but no audio → create ticket for Endpoint Support (driver/headset).
3. If web app works but desktop fails → reinstall Teams.
4. If verified and resolved → record resolution ticket.

Escalation:
Group: Service Desk (triage) → Network Team / Endpoint Support
Priority: Medium (High if executive/VIP or company-wide)