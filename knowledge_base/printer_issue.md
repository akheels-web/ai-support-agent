Title: Printer / Print Queue Issues

Important:
- AI must not install printer drivers remotely.
- Verify caller and asset tag if possible.
- Most issues are local queue or driver related.

Questions:
1. What printer model? (Check label on printer or print test page for model)
2. Is it a network printer (IP) or USB-connected?
3. What is the symptom? (Stuck in queue, prints blank, error light, "driver unavailable", wrong tray)
4. Can others print to the same printer?
5. Are you on VPN or office network?

Troubleshooting:
1. Clear print queue: Settings → Devices → Printers → Open Queue → Cancel All Documents.
2. Restart Print Spooler: Win+R → services.msc → Print Spooler → Restart.
3. If network printer on VPN: Ensure VPN connected. Try printer IP in browser (http://printer-ip) to verify reachable.
4. If "Driver unavailable": Reinstall printer via Settings → Add device → The printer I want isn't listed → Add by IP.
5. If prints blank/garbled: Replace toner/ink. Check paper size settings match tray.
6. If error light on printer: Check paper jam, toner low, cover open. Power cycle printer.
7. For shared printers: Ensure host PC is on and sharing enabled.

Action:
1. If only one user affected → create ticket for Endpoint Support (driver/queue).
2. If multiple users affected → create ticket for Print Services / Infrastructure.
3. If hardware fault (jam, toner, hardware error) → create Hardware Request ticket (requires Manager Approval).
4. If verified and resolved → record resolution ticket.

Escalation:
Group: Service Desk (triage) → Endpoint Support / Print Services
Priority: Low (Medium if department printer down)