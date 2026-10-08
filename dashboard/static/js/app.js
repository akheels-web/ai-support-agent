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
window.currentMetricsRange = '1d';

async function refreshTelemetryStats(range = null) {
    try {
        const activeRange = range || window.currentMetricsRange || '1d';
        const resp = await fetch(`/api/dashboard/stats?range=${encodeURIComponent(activeRange)}`);
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

window.setTimeRange = async function(range) {
    window.currentMetricsRange = range;

    // Update active button state in segmented control
    document.querySelectorAll('.time-range-btn').forEach(btn => {
        if (btn.getAttribute('data-range') === range) {
            btn.classList.add('active');
        } else {
            btn.classList.remove('active');
        }
    });

    // Update chart card title
    const chartTitle = document.getElementById('volumeTrendCardTitle');
    const chartDesc = document.getElementById('volumeTrendCardDesc');
    if (chartTitle) {
        if (range === '1d') {
            chartTitle.textContent = '24-Hour Call Volume & AI Deflection Trend';
            if (chartDesc) chartDesc.textContent = 'Hourly traffic distribution comparing total volume against autonomous deflection';
        } else if (range === '7d') {
            chartTitle.textContent = '7-Day Call Volume & AI Deflection Trend';
            if (chartDesc) chartDesc.textContent = 'Daily traffic volume and resolution patterns over the past 7 days';
        } else if (range === '30d') {
            chartTitle.textContent = '30-Day Call Volume & AI Deflection Trend';
            if (chartDesc) chartDesc.textContent = 'Monthly call volume metrics and deflection trajectory over 30 days';
        }
    }

    // Refresh KPI telemetry stats
    await refreshTelemetryStats(range);

    // Refresh charts
    if (window.updateDashboardCharts) {
        await window.updateDashboardCharts(range);
    }

    // Update URL query param smoothly
    const url = new URL(window.location);
    url.searchParams.set('range', range);
    window.history.replaceState({}, '', url);
};

const STATUS_LABELS = {
    'language_selected': 'Language Chosen',
    'in_progress': 'In Progress',
    'verified': 'Caller Verified',
    'troubleshooting': 'Troubleshooting',
    'ai_deflected': 'AI Deflected',
    'AI_Resolved': 'AI Deflected',
    'transferred': 'Transferred to Agent',
    'emergency_escalated': 'Sev-1 Escalation',
    'ended': 'Completed',
    'completed': 'Completed',
    'failed_verification': 'Verification Failed',
    'failed': 'Failed'
};

function humanStatus(status) {
    if (!status) return 'In Progress';
    if (STATUS_LABELS[status]) return STATUS_LABELS[status];
    return status.replace(/[_-]/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
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
                <td><span class="badge badge-success"><span class="pulse-dot"></span> ${humanStatus(r.status)}</span></td>
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
    if (status === 'language_selected') {
        return `<span class="badge badge-secondary">Language Chosen</span>`;
    }
    if (status === 'troubleshooting') {
        return `<span class="badge badge-secondary">Troubleshooting</span>`;
    }
    if (status && status.includes('fail')) {
        return `<span class="badge badge-destructive">⚠ ${humanStatus(status)}</span>`;
    }
    return `<span class="badge badge-secondary">${humanStatus(status)}</span>`;
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
// 5. User Profile Dropdown Menu
// ============================================================================
function toggleProfileDropdown(event) {
    if (event) {
        event.stopPropagation();
    }
    const menu = document.getElementById('profileMenu');
    const trigger = document.getElementById('profileDropdownTrigger');
    if (!menu) return;
    const isVisible = menu.classList.contains('show') || menu.style.display === 'block';
    if (isVisible) {
        menu.classList.remove('show');
        menu.style.display = 'none';
        if (trigger) trigger.setAttribute('aria-expanded', 'false');
    } else {
        menu.classList.add('show');
        menu.style.display = 'block';
        if (trigger) trigger.setAttribute('aria-expanded', 'true');
    }
}

// Close profile dropdown when clicking outside
document.addEventListener('click', (e) => {
    const wrapper = document.getElementById('profileDropdownWrapper');
    if (wrapper && !wrapper.contains(e.target)) {
        const menu = document.getElementById('profileMenu');
        const trigger = document.getElementById('profileDropdownTrigger');
        if (menu && (menu.classList.contains('show') || menu.style.display === 'block')) {
            menu.classList.remove('show');
            menu.style.display = 'none';
            if (trigger) trigger.setAttribute('aria-expanded', 'false');
        }
    }
});

// ============================================================================
// 7. Modern Toast Notification System
// ============================================================================
window.showToast = function({ title, message, type = 'success', duration = 3800 }) {
    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    toast.className = `toast-item toast-${type}`;

    let iconName = 'check-circle-2';
    if (type === 'error') iconName = 'alert-octagon';
    else if (type === 'warning') iconName = 'alert-triangle';
    else if (type === 'info') iconName = 'info';

    toast.innerHTML = `
        <div class="toast-icon">
            <i data-lucide="${iconName}" style="width: 18px; height: 18px;"></i>
        </div>
        <div class="toast-content">
            <div class="toast-title">${title || 'Notification'}</div>
            ${message ? `<div class="toast-message">${message}</div>` : ''}
        </div>
        <button type="button" class="toast-close-btn" aria-label="Close">
            <i data-lucide="x" style="width: 14px; height: 14px;"></i>
        </button>
    `;

    container.appendChild(toast);
    if (window.lucide) {
        try { lucide.createIcons({ root: toast }); } catch (e) { lucide.createIcons(); }
    }

    const closeToast = () => {
        toast.classList.add('toast-leave');
        setTimeout(() => {
            if (toast.parentNode) toast.parentNode.removeChild(toast);
        }, 260);
    };

    const closeBtn = toast.querySelector('.toast-close-btn');
    if (closeBtn) closeBtn.addEventListener('click', closeToast);

    if (duration > 0) {
        setTimeout(closeToast, duration);
    }
    return toast;
};

// ============================================================================
// 8. DOM Initialization & URL State
// ============================================================================
document.addEventListener('DOMContentLoaded', () => {
    initTheme();

    // Check URL parameters for range or success flashes
    const urlParams = new URLSearchParams(window.location.search);
    const rangeParam = urlParams.get('range');
    if (rangeParam && ['1d', '7d', '30d'].includes(rangeParam)) {
        window.currentMetricsRange = rangeParam;
        document.querySelectorAll('.time-range-btn').forEach(btn => {
            if (btn.getAttribute('data-range') === rangeParam) {
                btn.classList.add('active');
            } else {
                btn.classList.remove('active');
            }
        });
    }

    if (urlParams.get('saved') === '1' || urlParams.get('success') === '1') {
        const msg = urlParams.get('msg') || 'Action completed successfully.';
        window.showToast({
            title: 'Success',
            message: msg,
            type: 'success'
        });
        urlParams.delete('saved');
        urlParams.delete('success');
        urlParams.delete('msg');
        const newSearch = urlParams.toString();
        const newUrl = window.location.pathname + (newSearch ? '?' + newSearch : '');
        window.history.replaceState({}, '', newUrl);
    }

    // Setup auto-refresh intervals
    refreshTelemetryStats();
    telemetryTimer = setInterval(() => refreshTelemetryStats(), 30000); // 30s

    if (document.getElementById('active-calls-tbody')) {
        refreshActiveCalls();
        liveCallsTimer = setInterval(refreshActiveCalls, 10000); // 10s
    }

    // Bind search input for calls table if present
    initDataTableSearch('calls-table', 'table-search-input');

    // Close drawer or dropdown on ESC key
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            closeCallDrawer();
            const menu = document.getElementById('profileMenu');
            const trigger = document.getElementById('profileDropdownTrigger');
            if (menu && (menu.classList.contains('show') || menu.style.display === 'block')) {
                menu.classList.remove('show');
                menu.style.display = 'none';
                if (trigger) trigger.setAttribute('aria-expanded', 'false');
            }
        }
    });
});



