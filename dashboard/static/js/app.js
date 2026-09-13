/**
 * National Finance AI Support Operations Dashboard - App Engine
 * Handles theme switching, live telemetry auto-refresh, table filtering, and drawers.
 */

// ============================================================================
// 1. Theme Switcher (Corporate Light Mode default with Dark Mode toggle)
// ============================================================================
function initTheme() {
    const savedTheme = localStorage.getItem('nf_dashboard_theme');
    if (savedTheme === 'dark') {
        document.documentElement.classList.add('dark');
    } else {
        document.documentElement.classList.remove('dark');
    }
}

function toggleTheme() {
    const isDark = document.documentElement.classList.toggle('dark');
    localStorage.setItem('nf_dashboard_theme', isDark ? 'dark' : 'light');
    if (window.updateChartsTheme) {
        window.updateChartsTheme(isDark);
    }
}

// ============================================================================
// 2. Telemetry Auto-Refresh (30s polling for stats, 10s for live calls)
// ============================================================================
let telemetryTimer = null;
let liveCallsTimer = null;

async function refreshTelemetryStats() {
    try {
        const resp = await fetch('/api/dashboard/stats');
        if (!resp.ok) return;
        const data = await resp.json();

        // Update KPI values in DOM if present
        const updateElem = (id, val) => {
            const el = document.getElementById(id);
            if (el) el.textContent = val;
        };

        updateElem('kpi-total-calls', data.total_calls ?? '--');
        updateElem('kpi-deflection-rate', (data.deflection_rate ?? 0) + '%');
        updateElem('kpi-ongoing-calls', data.ongoing ?? '0');
        updateElem('kpi-emergency-calls', data.emergency_calls ?? '0');
        updateElem('kpi-vip-calls', data.vip_calls ?? '0');
        updateElem('kpi-tickets', data.tickets ?? '0');
        updateElem('kpi-verified', data.verified ?? '0');
        updateElem('kpi-failed', data.failed ?? '0');

        // Update live badge in sidebar
        const liveBadge = document.getElementById('sidebar-live-badge');
        if (liveBadge) {
            if (data.ongoing > 0) {
                liveBadge.textContent = data.ongoing;
                liveBadge.style.display = 'inline-block';
            } else {
                liveBadge.style.display = 'none';
            }
        }
    } catch (err) {
        console.warn('Telemetry refresh failed:', err);
    }
}

async function refreshActiveCalls() {
    const container = document.getElementById('active-calls-tbody');
    if (!container) return; // Not on active calls page

    try {
        const resp = await fetch('/api/active-calls/data');
        if (!resp.ok) return;
        const data = await resp.json();

        if (data.length === 0) {
            container.innerHTML = `<tr><td colspan="8" style="text-align:center; padding: 2rem; color: hsl(var(--muted-foreground));">No active calls in progress. System idle.</td></tr>`;
            return;
        }

        let html = '';
        data.forEach(r => {
            html += `
            <tr onclick="openCallDrawer('${r.call_id || r.id}')">
                <td>${r.id}</td>
                <td><code>${r.call_id || '--'}</code></td>
                <td><strong>${r.caller_id || '--'}</strong></td>
                <td>${r.caller_name || 'Unknown'}</td>
                <td>${r.employee_id || '--'}</td>
                <td><span class="badge badge-success"><span class="pulse-dot"></span> ${r.status || 'in_progress'}</span></td>
                <td>${r.duration_minutes || '0.1'}m</td>
                <td>${r.started_at || ''}</td>
            </tr>`;
        });
        container.innerHTML = html;
    } catch (err) {
        console.warn('Active calls refresh failed:', err);
    }
}

// ============================================================================
// 3. Call Inspection Drawer
// ============================================================================
async function openCallDrawer(callIdentifier) {
    const backdrop = document.getElementById('call-drawer-backdrop');
    if (!backdrop) return;

    backdrop.classList.add('open');
    document.body.style.overflow = 'hidden';

    // Set loading state in drawer
    const drawerTitle = document.getElementById('drawer-call-title');
    const drawerDetails = document.getElementById('drawer-call-content');
    if (drawerTitle) drawerTitle.textContent = `Call #${callIdentifier}`;
    if (drawerDetails) drawerDetails.innerHTML = '<div style="padding:2rem; text-align:center; color:hsl(var(--muted-foreground));">Loading call telemetry...</div>';

    try {
        const resp = await fetch(`/api/calls/${encodeURIComponent(callIdentifier)}`);
        if (!resp.ok) {
            drawerDetails.innerHTML = '<div style="padding:2rem; color:var(--nf-crimson);">Failed to load call details.</div>';
            return;
        }
        const call = await resp.json();

        const statusBadge = getStatusBadgeHtml(call.status, call.tier);
        const recordingPlayer = call.recording_file ? `
            <div class="card" style="margin-top:1rem; padding:1rem;">
                <div style="font-weight:600; font-size:0.8125rem; margin-bottom:0.5rem;">Call Recording Playback</div>
                <audio controls style="width:100%;">
                    <source src="/recordings/play?file=${encodeURIComponent(call.recording_file)}" type="audio/wav">
                    Your browser does not support audio playback.
                </audio>
            </div>
        ` : '';

        const ticketBlock = call.ticket_number ? `
            <div class="badge badge-destructive" style="font-size:0.8125rem; padding:0.4rem 0.75rem;">
                Ticket Created: #${call.ticket_number}
            </div>
        ` : '<span class="badge badge-secondary">No Ticket Required</span>';

        drawerDetails.innerHTML = `
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:1rem;">
                <div>${statusBadge}</div>
                <div>${ticketBlock}</div>
            </div>

            <div class="card" style="padding:1rem; margin-bottom:1rem;">
                <div style="font-weight:700; font-size:0.875rem; color:var(--nf-navy); margin-bottom:0.75rem;">Caller Identity & Enterprise Profile</div>
                <div style="display:grid; grid-template-columns: 1fr 1fr; gap:0.75rem; font-size:0.8125rem;">
                    <div><span style="color:hsl(var(--muted-foreground));">Name:</span> <strong>${call.verified_name || 'Unverified'}</strong></div>
                    <div><span style="color:hsl(var(--muted-foreground));">Employee ID:</span> <strong>${call.employee_id || 'N/A'}</strong></div>
                    <div><span style="color:hsl(var(--muted-foreground));">Caller ID:</span> <strong>${call.caller_number || 'Unknown'}</strong></div>
                    <div><span style="color:hsl(var(--muted-foreground));">Tier:</span> <span class="badge ${call.tier === 'P0_EXECUTIVE' ? 'badge-vip' : 'badge-secondary'}">${call.tier || 'STANDARD'}</span></div>
                    <div><span style="color:hsl(var(--muted-foreground));">Language:</span> <strong>${call.language === 'ar' ? 'Arabic' : 'English'}</strong></div>
                    <div><span style="color:hsl(var(--muted-foreground));">Duration:</span> <strong>${call.duration_minutes || '0'} mins</strong></div>
                </div>
            </div>

            <div class="card" style="padding:1rem; margin-bottom:1rem;">
                <div style="font-weight:700; font-size:0.875rem; color:var(--nf-navy); margin-bottom:0.5rem;">AI Incident Summary & Telemetry</div>
                <p style="font-size:0.875rem; line-height:1.6; color:hsl(var(--foreground));">${call.summary || 'No summary recorded for this session.'}</p>
            </div>

            ${(() => {
                if (!call.transcript) {
                    return `
                        <div class="card" style="padding:1rem; margin-bottom:1rem;">
                            <div style="font-weight:700; font-size:0.875rem; color:var(--nf-navy); margin-bottom:0.5rem;">Conversation Dialogue Transcript</div>
                            <div style="font-size:0.8125rem; color:hsl(var(--muted-foreground)); font-style:italic;">No spoken transcript recorded for this session.</div>
                        </div>
                    `;
                }

                const lines = call.transcript.split('\n').filter(l => l.trim().length > 0);
                const bubbles = lines.map(line => {
                    const isArif = line.includes('Arif:');
                    const bubbleStyle = isArif 
                        ? 'background: hsl(var(--primary)/0.08); border-left: 3px solid hsl(var(--primary));'
                        : 'background: hsl(var(--muted)/0.4); border-left: 3px solid hsl(var(--muted-foreground));';
                    const speakerName = isArif ? '🤖 Arif (AI Support Agent)' : `👤 ${call.verified_name || 'Caller'}`;
                    const timeMatch = line.match(/^\[(.*?)\]/);
                    const timeTag = timeMatch ? `<span style="font-size:0.7rem; opacity:0.65; margin-left:0.5rem; font-weight:normal;">${timeMatch[1]}</span>` : '';
                    const messageText = line.replace(/^\[.*?\]\s*(Caller|Arif):\s*/i, '');

                    return `
                        <div style="margin-bottom:0.6rem; padding:0.6rem 0.8rem; border-radius:6px; font-size:0.8125rem; ${bubbleStyle}">
                            <div style="font-weight:600; font-size:0.75rem; margin-bottom:0.25rem; display:flex; justify-content:space-between; align-items:center;">
                                <span>${speakerName}</span>
                                ${timeTag}
                            </div>
                            <div style="line-height:1.5; color:hsl(var(--foreground));">${messageText}</div>
                        </div>
                    `;
                }).join('');

                return `
                    <div class="card" style="padding:1rem; margin-bottom:1rem;">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.75rem;">
                            <div style="font-weight:700; font-size:0.875rem; color:var(--nf-navy);">Conversation Dialogue Transcript</div>
                            <span class="badge badge-secondary" style="font-size:0.7rem;">${lines.length} Turns</span>
                        </div>
                        <div style="max-height:300px; overflow-y:auto; padding-right:0.4rem;">
                            ${bubbles}
                        </div>
                    </div>
                `;
            })()}

            ${recordingPlayer}
        `;
    } catch (err) {
        drawerDetails.innerHTML = `<div style="padding:2rem; color:var(--nf-crimson);">Error: ${err.message}</div>`;
    }
}

function closeCallDrawer() {
    const backdrop = document.getElementById('call-drawer-backdrop');
    if (backdrop) backdrop.classList.remove('open');
    document.body.style.overflow = '';
}

function getStatusBadgeHtml(status, tier) {
    if (tier === 'CRITICAL' || status === 'emergency_escalated') {
        return `<span class="badge badge-critical">🚨 Sev-1 Critical Escalation</span>`;
    }
    if (status === 'AI_Resolved' || status === 'ai_deflected') {
        return `<span class="badge badge-success">✓ AI Deflected (First-Contact)</span>`;
    }
    if (status === 'transferred') {
        return `<span class="badge badge-warning">↗ Transferred to Agent</span>`;
    }
    if (status === 'verified') {
        return `<span class="badge badge-default">✓ Caller Verified</span>`;
    }
    if (status && status.includes('fail')) {
        return `<span class="badge badge-destructive">⚠ ${status}</span>`;
    }
    return `<span class="badge badge-secondary">${status || 'Completed'}</span>`;
}

// ============================================================================
// 4. Interactive Client Data Table (Filter, Search & Pagination)
// ============================================================================
function initDataTableSearch(tableId, searchInputId) {
    const input = document.getElementById(searchInputId);
    const table = document.getElementById(tableId);
    if (!input || !table) return;

    input.addEventListener('input', (e) => {
        const query = e.target.value.toLowerCase().trim();
        const rows = table.querySelectorAll('tbody tr');

        rows.forEach(row => {
            const text = row.textContent.toLowerCase();
            row.style.display = text.includes(query) ? '' : 'none';
        });
    });
}

function sortTableByColumn(table, colIndex, isAsc = true) {
    const tbody = table.querySelector('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr'));

    rows.sort((a, b) => {
        const cellA = a.children[colIndex].textContent.trim();
        const cellB = b.children[colIndex].textContent.trim();

        const numA = parseFloat(cellA);
        const numB = parseFloat(cellB);
        if (!isNaN(numA) && !isNaN(numB)) {
            return isAsc ? numA - numB : numB - numA;
        }
        return isAsc ? cellA.localeCompare(cellB) : cellB.localeCompare(cellA);
    });

    rows.forEach(row => tbody.appendChild(row));
}

// ============================================================================
// 5. DOM Initialization
// ============================================================================
document.addEventListener('DOMContentLoaded', () => {
    initTheme();

    // Setup auto-refresh intervals
    refreshTelemetryStats();
    telemetryTimer = setInterval(refreshTelemetryStats, 30000); // 30s

    if (document.getElementById('active-calls-tbody')) {
        refreshActiveCalls();
        liveCallsTimer = setInterval(refreshActiveCalls, 10000); // 10s
    }

    // Bind search input for calls table
    initDataTableSearch('calls-table', 'table-search-input');

    // Close drawer on ESC key
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeCallDrawer();
    });
});
