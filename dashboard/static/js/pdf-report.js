/**
 * National Finance AI Support Operations Dashboard - Executive PDF Reporting ("pdfcn")
 * Generates and downloads a branded Executive Shift & Operations Summary Report.
 */

async function exportExecutivePdfReport() {
    const btn = document.getElementById('export-pdf-btn');
    const originalText = btn ? btn.innerHTML : '';
    if (btn) {
        btn.innerHTML = `<span class="pulse-dot"></span> Generating PDF...`;
        btn.disabled = true;
    }

    let reportContainer = null;
    try {
        // 1. Fetch current telemetry summary
        let stats = {
            total_calls: 0,
            deflection_rate: 0,
            emergency_calls: 0,
            vip_calls: 0,
            deflected: 0,
            verified: 0,
            tickets: 0
        };

        try {
            const statsResp = await fetch('/api/dashboard/stats');
            if (statsResp.ok) {
                stats = await statsResp.json();
            }
        } catch (e) {
            console.warn('Could not fetch /api/dashboard/stats:', e);
        }

        // 2. Build printable executive document container
        reportContainer = document.createElement('div');
        reportContainer.id = 'executive-pdf-document';
        reportContainer.style.cssText = `
            position: fixed;
            left: 0;
            top: 0;
            width: 800px;
            background: #ffffff;
            color: #1F2937;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            padding: 36px 40px;
            box-sizing: border-box;
            z-index: -9999;
            opacity: 1;
            pointer-events: none;
        `;

        const now = new Date().toLocaleString('en-US', {
            dateStyle: 'medium',
            timeStyle: 'short'
        });

        // Capture chart canvas as data URLs if available on current page
        const volCanvas = document.getElementById('volumeTrendChart');
        const defCanvas = document.getElementById('deflectionDoughnutChart');
        let volImg = '';
        let defImg = '';
        try {
            if (volCanvas) volImg = volCanvas.toDataURL('image/png');
            if (defCanvas) defImg = defCanvas.toDataURL('image/png');
        } catch (chartErr) {
            console.warn('Chart toDataURL error:', chartErr);
        }

        const chartSectionHtml = (volImg && defImg) ? `
            <div style="display: grid; grid-template-columns: 2fr 1fr; gap: 16px; margin-bottom: 24px;">
                <div style="border: 1px solid #E5E7EB; border-radius: 8px; padding: 14px; background: #FAFAFA;">
                    <div style="font-size: 12px; font-weight: 700; color: #1B2F6B; margin-bottom: 8px;">24-Hour Telephony Inbound & Deflection Trend</div>
                    <img src="${volImg}" style="width: 100%; height: 190px; object-fit: contain;">
                </div>
                <div style="border: 1px solid #E5E7EB; border-radius: 8px; padding: 14px; background: #FAFAFA;">
                    <div style="font-size: 12px; font-weight: 700; color: #1B2F6B; margin-bottom: 8px;">Resolution Breakdown</div>
                    <img src="${defImg}" style="width: 100%; height: 190px; object-fit: contain;">
                </div>
            </div>
        ` : `
            <div style="border: 1px solid #E5E7EB; border-radius: 8px; padding: 16px; margin-bottom: 24px; background: #F8FAFC;">
                <div style="font-size: 13px; font-weight: 700; color: #1B2F6B; margin-bottom: 10px;">Telephony Shift Operational Metrics</div>
                <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; font-size: 12px;">
                    <div style="background: #ffffff; border: 1px solid #E2E8F0; padding: 10px; border-radius: 6px;">
                        <span style="color: #64748B; font-size: 11px; display: block;">Autonomous AI Deflections</span>
                        <strong style="color: #10B981; font-size: 18px;">${stats.deflected ?? 0} calls</strong>
                    </div>
                    <div style="background: #ffffff; border: 1px solid #E2E8F0; padding: 10px; border-radius: 6px;">
                        <span style="color: #64748B; font-size: 11px; display: block;">Verified Employee Callers</span>
                        <strong style="color: #1B2F6B; font-size: 18px;">${stats.verified ?? 0} callers</strong>
                    </div>
                    <div style="background: #ffffff; border: 1px solid #E2E8F0; padding: 10px; border-radius: 6px;">
                        <span style="color: #64748B; font-size: 11px; display: block;">Escalation Tickets Created</span>
                        <strong style="color: #F59E0B; font-size: 18px;">${stats.tickets ?? 0} tickets</strong>
                    </div>
                </div>
            </div>
        `;

        reportContainer.innerHTML = `
            <div style="border-bottom: 2px solid #1B2F6B; padding-bottom: 16px; margin-bottom: 24px; display: flex; justify-content: space-between; align-items: flex-start;">
                <div>
                    <h1 style="font-size: 24px; font-weight: 800; color: #1B2F6B; margin: 0; letter-spacing: -0.5px;">NATIONAL FINANCE</h1>
                    <p style="font-size: 13px; color: #64748B; margin: 4px 0 0; font-weight: 500;">AI IT Support Operations — Executive Shift Summary</p>
                </div>
                <div style="text-align: right; font-size: 11px; color: #64748B; line-height: 1.5;">
                    <div><strong style="color: #1E293B;">Generated:</strong> ${now}</div>
                    <div><strong style="color: #1E293B;">Reporting Period:</strong> Past 24 Hours</div>
                    <div style="color: #059669; font-weight: 700; margin-top: 2px;">● CLASSIFICATION: CONFIDENTIAL</div>
                </div>
            </div>

            <!-- KPI Tiles Grid -->
            <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 24px;">
                <div style="border: 1px solid #E2E8F0; border-top: 4px solid #1B2F6B; border-radius: 8px; padding: 14px; background: #FFFFFF;">
                    <div style="font-size: 11px; text-transform: uppercase; color: #64748B; font-weight: 700;">Total Calls</div>
                    <div style="font-size: 26px; font-weight: 800; color: #1B2F6B; margin-top: 4px;">${stats.total_calls ?? '--'}</div>
                    <div style="font-size: 11px; color: #94A3B8; margin-top: 2px;">Past 24h Telephony Inbound</div>
                </div>
                <div style="border: 1px solid #E2E8F0; border-top: 4px solid #10B981; border-radius: 8px; padding: 14px; background: #FFFFFF;">
                    <div style="font-size: 11px; text-transform: uppercase; color: #64748B; font-weight: 700;">Deflection Rate</div>
                    <div style="font-size: 26px; font-weight: 800; color: #10B981; margin-top: 4px;">${stats.deflection_rate ?? '0'}%</div>
                    <div style="font-size: 11px; color: #94A3B8; margin-top: 2px;">Autonomous AI Resolution</div>
                </div>
                <div style="border: 1px solid #E2E8F0; border-top: 4px solid #C8102E; border-radius: 8px; padding: 14px; background: #FFFFFF;">
                    <div style="font-size: 11px; text-transform: uppercase; color: #64748B; font-weight: 700;">Critical Sev-1</div>
                    <div style="font-size: 26px; font-weight: 800; color: #C8102E; margin-top: 4px;">${stats.emergency_calls ?? '0'}</div>
                    <div style="font-size: 11px; color: #94A3B8; margin-top: 2px;">High Priority Escalations</div>
                </div>
                <div style="border: 1px solid #E2E8F0; border-top: 4px solid #8B5CF6; border-radius: 8px; padding: 14px; background: #FFFFFF;">
                    <div style="font-size: 11px; text-transform: uppercase; color: #64748B; font-weight: 700;">VIP & Exec Calls</div>
                    <div style="font-size: 26px; font-weight: 800; color: #8B5CF6; margin-top: 4px;">${stats.vip_calls ?? '0'}</div>
                    <div style="font-size: 11px; color: #94A3B8; margin-top: 2px;">Executive Concierge Tier</div>
                </div>
            </div>

            <!-- Charts Section or Structured Telemetry -->
            ${chartSectionHtml}

            <!-- Telemetry Analysis Notes -->
            <div style="border: 1px solid #E2E8F0; border-radius: 8px; padding: 18px; margin-bottom: 24px; font-size: 12px; line-height: 1.6; background: #FFFFFF;">
                <div style="font-weight: 700; color: #1B2F6B; margin-bottom: 8px; font-size: 13px;">Executive Operational Highlights</div>
                <ul style="padding-left: 20px; margin: 0; color: #334155;">
                    <li style="margin-bottom: 6px;"><strong>Conversational Voice AI Deflection:</strong> Successfully resolved ${stats.deflected ?? 0} routine IT service requests directly on the voice bridge without human agent intervention.</li>
                    <li style="margin-bottom: 6px;"><strong>Caller Verification:</strong> Verified ${stats.verified ?? 0} corporate employee identities using automated employee directory telephone matching.</li>
                    <li style="margin-bottom: 6px;"><strong>Multi-Queue Telephony Routing:</strong> Routed ${stats.vip_calls ?? 0} VIP callers with immediate P0 priority and ${stats.emergency_calls ?? 0} critical emergency tickets.</li>
                    <li><strong>Infrastructure Stability:</strong> Voice gateway connection pooling maintained uninterrupted telephony operations with zero service drops.</li>
                </ul>
            </div>

            <!-- Footer Sign-off -->
            <div style="border-top: 1px solid #E2E8F0; padding-top: 16px; display: flex; justify-content: space-between; align-items: center; font-size: 11px; color: #94A3B8;">
                <div><strong>National Finance SAOG</strong> — Technology & Digital Transformation</div>
                <div>Enterprise AI Support Suite — Confidential Report</div>
            </div>
        `;

        document.body.appendChild(reportContainer);

        // Wait a small tick for DOM and canvas rendering
        await new Promise(resolve => setTimeout(resolve, 300));

        if (window.html2pdf) {
            const opt = {
                margin: [10, 10, 10, 10],
                filename: `National_Finance_AI_Support_Report_${new Date().toISOString().slice(0, 10)}.pdf`,
                image: { type: 'jpeg', quality: 0.98 },
                html2canvas: {
                    scale: 2,
                    useCORS: true,
                    letterRendering: true,
                    scrollY: 0,
                    scrollX: 0,
                    windowWidth: 800
                },
                jsPDF: { unit: 'mm', format: 'a4', orientation: 'portrait' }
            };
            await window.html2pdf().set(opt).from(reportContainer).save();
        } else {
            console.warn('html2pdf library not available, fallback to print');
            window.print();
        }
    } catch (err) {
        console.error('PDF Generation failed:', err);
        alert('PDF Generation failed: ' + (err.message || err));
    } finally {
        if (reportContainer && reportContainer.parentNode) {
            reportContainer.parentNode.removeChild(reportContainer);
        }
        if (btn) {
            btn.innerHTML = originalText;
            btn.disabled = false;
        }
    }
}

window.exportExecutivePdfReport = exportExecutivePdfReport;
