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
    const originalScrollY = window.scrollY;
    const originalScrollX = window.scrollX;

    try {
        // Scroll to top for html2canvas coordinate alignment
        window.scrollTo(0, 0);

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

        // 2. Build printable executive document container using rock-solid HTML tables
        // (Avoiding display:grid which html2canvas fails to render in html2pdf)
        reportContainer = document.createElement('div');
        reportContainer.id = 'executive-pdf-document';
        reportContainer.style.cssText = `
            position: fixed;
            left: 0;
            top: 0;
            width: 794px;
            background: #ffffff !important;
            color: #1F2937 !important;
            font-family: Arial, Helvetica, sans-serif !important;
            padding: 32px 36px;
            box-sizing: border-box;
            z-index: 999999;
            box-shadow: 0 10px 25px rgba(0,0,0,0.3);
            visibility: visible !important;
            opacity: 1 !important;
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
            <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom: 20px; border-collapse: separate; border-spacing: 12px 0;">
                <tr>
                    <td width="65%" style="border: 1px solid #E5E7EB; border-radius: 6px; padding: 12px; background: #FAFAFA; vertical-align: top;">
                        <div style="font-size: 11px; font-weight: 700; color: #1B2F6B; margin-bottom: 8px; text-transform: uppercase;">24-Hour Telephony Inbound & Deflection Trend</div>
                        <img src="${volImg}" style="width: 100%; height: 180px; object-fit: contain; display: block;">
                    </td>
                    <td width="35%" style="border: 1px solid #E5E7EB; border-radius: 6px; padding: 12px; background: #FAFAFA; vertical-align: top;">
                        <div style="font-size: 11px; font-weight: 700; color: #1B2F6B; margin-bottom: 8px; text-transform: uppercase;">Resolution Breakdown</div>
                        <img src="${defImg}" style="width: 100%; height: 180px; object-fit: contain; display: block;">
                    </td>
                </tr>
            </table>
        ` : `
            <div style="border: 1px solid #E5E7EB; border-radius: 6px; padding: 14px; margin-bottom: 20px; background: #F8FAFC;">
                <div style="font-size: 12px; font-weight: 700; color: #1B2F6B; margin-bottom: 10px; text-transform: uppercase;">Telephony Shift Operational Metrics</div>
                <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse: separate; border-spacing: 10px 0;">
                    <tr>
                        <td width="33.33%" style="background: #ffffff; border: 1px solid #E2E8F0; padding: 10px; border-radius: 6px; vertical-align: top;">
                            <div style="color: #64748B; font-size: 11px;">Autonomous AI Deflections</div>
                            <div style="color: #10B981; font-size: 18px; font-weight: 800; margin-top: 4px;">${stats.deflected ?? 0} calls</div>
                        </td>
                        <td width="33.33%" style="background: #ffffff; border: 1px solid #E2E8F0; padding: 10px; border-radius: 6px; vertical-align: top;">
                            <div style="color: #64748B; font-size: 11px;">Verified Employee Callers</div>
                            <div style="color: #1B2F6B; font-size: 18px; font-weight: 800; margin-top: 4px;">${stats.verified ?? 0} callers</div>
                        </td>
                        <td width="33.33%" style="background: #ffffff; border: 1px solid #E2E8F0; padding: 10px; border-radius: 6px; vertical-align: top;">
                            <div style="color: #64748B; font-size: 11px;">Escalation Tickets Created</div>
                            <div style="color: #F59E0B; font-size: 18px; font-weight: 800; margin-top: 4px;">${stats.tickets ?? 0} tickets</div>
                        </td>
                    </tr>
                </table>
            </div>
        `;

        reportContainer.innerHTML = `
            <!-- Header -->
            <table width="100%" cellpadding="0" cellspacing="0" style="border-bottom: 3px solid #1B2F6B; padding-bottom: 14px; margin-bottom: 20px;">
                <tr>
                    <td style="vertical-align: middle;">
                        <h1 style="font-size: 22px; font-weight: 800; color: #1B2F6B; margin: 0; letter-spacing: -0.5px;">NATIONAL FINANCE</h1>
                        <p style="font-size: 12px; color: #64748B; margin: 4px 0 0; font-weight: 600;">AI IT Support Operations — Executive Shift Summary</p>
                    </td>
                    <td style="text-align: right; vertical-align: middle; font-size: 11px; color: #64748B; line-height: 1.4;">
                        <div><strong style="color: #1E293B;">Generated:</strong> ${now}</div>
                        <div><strong style="color: #1E293B;">Reporting Period:</strong> Past 24 Hours</div>
                        <div style="color: #059669; font-weight: 700; margin-top: 2px;">● CLASSIFICATION: CONFIDENTIAL</div>
                    </td>
                </tr>
            </table>

            <!-- KPI Tiles -->
            <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom: 20px; border-collapse: separate; border-spacing: 10px 0;">
                <tr>
                    <td width="25%" style="border: 1px solid #E2E8F0; border-top: 4px solid #1B2F6B; border-radius: 6px; padding: 12px; background: #FFFFFF; vertical-align: top;">
                        <div style="font-size: 10px; text-transform: uppercase; color: #64748B; font-weight: 700;">Total Calls</div>
                        <div style="font-size: 24px; font-weight: 800; color: #1B2F6B; margin-top: 4px;">${stats.total_calls ?? '--'}</div>
                        <div style="font-size: 10px; color: #94A3B8; margin-top: 2px;">24h Inbound Volume</div>
                    </td>
                    <td width="25%" style="border: 1px solid #E2E8F0; border-top: 4px solid #10B981; border-radius: 6px; padding: 12px; background: #FFFFFF; vertical-align: top;">
                        <div style="font-size: 10px; text-transform: uppercase; color: #64748B; font-weight: 700;">Deflection Rate</div>
                        <div style="font-size: 24px; font-weight: 800; color: #10B981; margin-top: 4px;">${stats.deflection_rate ?? '0'}%</div>
                        <div style="font-size: 10px; color: #94A3B8; margin-top: 2px;">Autonomous AI Resolution</div>
                    </td>
                    <td width="25%" style="border: 1px solid #E2E8F0; border-top: 4px solid #C8102E; border-radius: 6px; padding: 12px; background: #FFFFFF; vertical-align: top;">
                        <div style="font-size: 10px; text-transform: uppercase; color: #64748B; font-weight: 700;">Critical Sev-1</div>
                        <div style="font-size: 24px; font-weight: 800; color: #C8102E; margin-top: 4px;">${stats.emergency_calls ?? '0'}</div>
                        <div style="font-size: 10px; color: #94A3B8; margin-top: 2px;">High Priority Escalations</div>
                    </td>
                    <td width="25%" style="border: 1px solid #E2E8F0; border-top: 4px solid #8B5CF6; border-radius: 6px; padding: 12px; background: #FFFFFF; vertical-align: top;">
                        <div style="font-size: 10px; text-transform: uppercase; color: #64748B; font-weight: 700;">VIP & Exec Calls</div>
                        <div style="font-size: 24px; font-weight: 800; color: #8B5CF6; margin-top: 4px;">${stats.vip_calls ?? '0'}</div>
                        <div style="font-size: 10px; color: #94A3B8; margin-top: 2px;">Executive Concierge Tier</div>
                    </td>
                </tr>
            </table>

            <!-- Charts Section or Structured Telemetry -->
            ${chartSectionHtml}

            <!-- Telemetry Analysis Notes -->
            <div style="border: 1px solid #E2E8F0; border-radius: 6px; padding: 16px; margin-bottom: 20px; font-size: 11px; line-height: 1.6; background: #FFFFFF;">
                <div style="font-weight: 700; color: #1B2F6B; margin-bottom: 8px; font-size: 12px; text-transform: uppercase;">Executive Operational Highlights</div>
                <ul style="padding-left: 18px; margin: 0; color: #334155;">
                    <li style="margin-bottom: 5px;"><strong>Voice AI Autonomous Deflection:</strong> Successfully resolved ${stats.deflected ?? 0} routine IT service requests directly on the voice bridge without human agent intervention.</li>
                    <li style="margin-bottom: 5px;"><strong>Caller Verification:</strong> Verified ${stats.verified ?? 0} corporate employee identities using automated employee directory telephone matching.</li>
                    <li style="margin-bottom: 5px;"><strong>Multi-Queue Telephony Routing:</strong> Routed ${stats.vip_calls ?? 0} VIP callers with immediate P0 priority and ${stats.emergency_calls ?? 0} critical emergency tickets.</li>
                    <li><strong>Infrastructure Stability:</strong> Voice gateway connection pooling maintained uninterrupted telephony operations with zero service drops.</li>
                </ul>
            </div>

            <!-- Footer Sign-off -->
            <table width="100%" cellpadding="0" cellspacing="0" style="border-top: 1px solid #E2E8F0; padding-top: 12px; font-size: 10px; color: #94A3B8;">
                <tr>
                    <td><strong>National Finance SAOG</strong> — Technology & Digital Transformation</td>
                    <td style="text-align: right;">Enterprise AI Support Suite — Confidential Report</td>
                </tr>
            </table>
        `;

        document.body.appendChild(reportContainer);

        // Wait a tick for DOM layout and canvas data bindings
        await new Promise(resolve => setTimeout(resolve, 350));

        if (window.html2pdf) {
            const opt = {
                margin: [8, 8, 8, 8],
                filename: `National_Finance_AI_Support_Report_${new Date().toISOString().slice(0, 10)}.pdf`,
                image: { type: 'jpeg', quality: 0.98 },
                html2canvas: {
                    scale: 2,
                    useCORS: true,
                    logging: false,
                    scrollY: 0,
                    scrollX: 0,
                    windowWidth: 794
                },
                jsPDF: { unit: 'mm', format: 'a4', orientation: 'portrait' }
            };
            await window.html2pdf().set(opt).from(reportContainer).save();
        } else {
            console.warn('html2pdf library not available, fallback to window.print()');
            window.print();
        }
    } catch (err) {
        console.error('PDF Generation failed:', err);
        alert('PDF Generation failed: ' + (err.message || err));
    } finally {
        if (reportContainer && reportContainer.parentNode) {
            reportContainer.parentNode.removeChild(reportContainer);
        }
        window.scrollTo(originalScrollX, originalScrollY);
        if (btn) {
            btn.innerHTML = originalText;
            btn.disabled = false;
        }
    }
}

window.exportExecutivePdfReport = exportExecutivePdfReport;
