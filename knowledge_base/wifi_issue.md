Title: Wi-Fi / Network Connectivity Issues

Important:
- AI must not configure network settings remotely.
- Verify caller and location (office, home, branch).
- Distinguish between Wi-Fi, wired, and VPN issues.

Questions:
1. Where are you? (Office building/floor, Home, Branch office, VPN)
2. What device? (Laptop, Phone, Tablet, Dock)
3. What is the symptom? (No internet, limited connectivity, "No internet secured", keeps disconnecting, slow)
4. Can you access internal resources? (File shares, Intranet, ERP)
5. Are others nearby having the same issue?

Troubleshooting:
1. If on office Wi-Fi: Forget "NF-Corp" / "NF-Guest" → Reconnect. Use corporate SSID, not guest.
2. If "No internet secured": Disconnect/Reconnect Wi-Fi. Toggle Airplane mode.
3. If on docking station: Undock → Redock. Try wired Ethernet directly.
4. If at home: Restart home router. Test with phone hotspot to isolate.
5. If VPN connected but no internal access: Disconnect/Reconnect VPN. Try different VPN gateway.
6. Run network reset: Settings → Network → Advanced → Network Reset (Windows 10/11).
7. Check IP config: Win+R → cmd → ipconfig /release → ipconfig /renew → ipconfig /flushdns.

Action:
1. If single device, office Wi-Fi → create ticket for Endpoint Support (Wi-Fi profile/driver).
2. If multiple users, same area → create ticket for Network Infrastructure (AP/Controller).
3. If VPN only → create ticket for Network Team (VPN gateway).
4. If branch office → create ticket for WAN/Branch Connectivity.
5. If verified and resolved → record resolution ticket.

Escalation:
Group: Service Desk (triage) → Network Infrastructure / Endpoint Support
Priority: Medium (High if entire floor/branch down)