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

    try {
        // 1. Fetch current telemetry summary
        const [statsResp, chartResp] = await Promise.all([
            fetch('/api/dashboard/stats'),
            fetch('/api/dashboard/chart-data')
        ]);
        const stats = await statsResp.json();
        const chartData = await chartResp.json();

        // 2. Build printable executive document container
        const reportContainer = document.createElement('div');
        reportContainer.id = 'executive-pdf-document';
        reportContainer.style.cssText = `
            position: absolute;
            left: -9999px;
            top: 0;
            width: 800px;
            background: #ffffff;
            color: #1F2937;
            font-family: 'Inter', -apple-system, sans-serif;
            padding: 32px;
            box-sizing: border-box;
        `;

        const now = new Date().toLocaleString();

        // Capture chart canvas as data URLs if available
        const volCanvas = document.getElementById('volumeTrendChart');
        const defCanvas = document.getElementById('deflectionDoughnutChart');
        const volImg = volCanvas ? volCanvas.toDataURL('image/png') : '';
        const defImg = defCanvas ? defCanvas.toDataURL('image/png') : '';

        reportContainer.innerHTML = `
            <div style="border-bottom: 2px solid #1B2F6B; padding-bottom: 16px; margin-bottom: 24px; display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <h1 style="font-size: 22px; font-weight: 800; color: #1B2F6B; margin: 0;">NATIONAL FINANCE</h1>
                    <p style="font-size: 13px; color: #6B7280; margin: 4px 0 0;">AI IT Support Operations — Executive Shift Summary</p>
                </div>
                <div style="text-align: right; font-size: 11px; color: #6B7280;">
                    <div><strong>Report Generated:</strong> ${now}</div>
                    <div><strong>Period:</strong> Past 24 Hours</div>
                    <div><strong>Classification:</strong> CONFIDENTIAL - INTERNAL USE</div>
                </div>
            </div>

            <!-- KPI Tiles Grid -->
            <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 24px;">
                <div style="border: 1px solid #E5E7EB; border-top: 3px solid #1B2F6B; border-radius: 8px; padding: 12px;">
                    <div style="font-size: 11px; text-transform: uppercase; color: #6B7280; font-weight: 600;">Total Calls</div>
                    <div style="font-size: 24px; font-weight: 800; color: #1B2F6B; margin-top: 4px;">${stats.total_calls ?? '--'}</div>
                </div>
                <div style="border: 1px solid #E5E7EB; border-top: 3px solid #10B981; border-radius: 8px; padding: 12px;">
                    <div style="font-size: 11px; text-transform: uppercase; color: #6B7280; font-weight: 600;">Deflection Rate</div>
                    <div style="font-size: 24px; font-weight: 800; color: #10B981; margin-top: 4px;">${stats.deflection_rate ?? '0'}%</div>
                </div>
                <div style="border: 1px solid #E5E7EB; border-top: 3px solid #C8102E; border-radius: 8px; padding: 12px;">
                    <div style="font-size: 11px; text-transform: uppercase; color: #6B7280; font-weight: 600;">Sev-1 Critical</div>
                    <div style="font-size: 24px; font-weight: 800; color: #C8102E; margin-top: 4px;">${stats.emergency_calls ?? '0'}</div>
                </div>
                <div style="border: 1px solid #E5E7EB; border-top: 3px solid #F59E0B; border-radius: 8px; padding: 12px;">
                    <div style="font-size: 11px; text-transform: uppercase; color: #6B7280; font-weight: 600;">VIP & Exec Calls</div>
                    <div style="font-size: 24px; font-weight: 800; color: #F59E0B; margin-top: 4px;">${stats.vip_calls ?? '0'}</div>
                </div>
            </div>

            <!-- Charts Section -->
            <div style="display: grid; grid-template-columns: 2fr 1fr; gap: 16px; margin-bottom: 24px;">
                <div style="border: 1px solid #E5E7EB; border-radius: 8px; padding: 12px;">
                    <div style="font-size: 12px; font-weight: 700; color: #1B2F6B; margin-bottom: 8px;">24-Hour Call Volume & AI Deflection Trend</div>
                    ${volImg ? `<img src="${volImg}" style="width: 100%; height: 180px; object-fit: contain;">` : '<p style="font-size:11px;color:#999;">Chart not available</p>'}
                </div>
                <div style="border: 1px solid #E5E7EB; border-radius: 8px; padding: 12px;">
                    <div style="font-size: 12px; font-weight: 700; color: #1B2F6B; margin-bottom: 8px;">Resolution Breakdown</div>
                    ${defImg ? `<img src="${defImg}" style="width: 100%; height: 180px; object-fit: contain;">` : '<p style="font-size:11px;color:#999;">Chart not available</p>'}
                </div>
            </div>

            <!-- Telemetry Analysis Notes -->
            <div style="border: 1px solid #E5E7EB; border-radius: 8px; padding: 16px; margin-bottom: 24px; font-size: 12px; line-height: 1.6;">
                <div style="font-weight: 700; color: #1B2F6B; margin-bottom: 6px;">Executive Operational Highlights</div>
                <ul style="padding-left: 18px; margin: 0; color: #4B5563;">
                    <li><strong>AI First-Contact Resolution:</strong> Successfully deflected ${stats.deflected ?? 0} routine IT queries directly via conversational voice AI.</li>
                    <li><strong>Caller Authentication:</strong> Verified ${stats.verified ?? 0} callers through employee database CLI identification.</li>
                    <li><strong>Escalation Handling:</strong> Multi-queue routing redirected ${stats.vip_calls ?? 0} C-Suite VIP inquiries to Queue 7002 and ${stats.emergency_calls ?? 0} critical incidents to Queue 7003.</li>
                    <li><strong>Zero-Lag PBX Architecture:</strong> High-performance connection pooling maintained uninterrupted telephony telemetry.</li>
                </ul>
            </div>

            <!-- Footer Sign-off -->
            <div style="border-top: 1px solid #E5E7EB; padding-top: 16px; display: flex; justify-content: space-between; align-items: center; font-size: 11px; color: #9CA3AF;">
                <div>National Finance Oman — Operations & Infrastructure</div>
                <div>Technology Partner: TCT Enterprise Solutions</div>
            </div>
        `;

        document.body.appendChild(reportContainer);

        // Check if html2pdf is available, otherwise trigger clean browser print
        if (window.html2pdf) {
            const opt = {
                margin: [10, 10, 10, 10],
                filename: `National_Finance_AI_Support_Report_${new Date().toISOString().slice(0,10)}.pdf`,
                image: { type: 'jpeg', quality: 0.98 },
                html2canvas: { scale: 2, useCORS: true },
                jsPDF: { unit: 'mm', format: 'a4', orientation: 'portrait' }
            };
            await window.html2pdf().set(opt).from(reportContainer).save();
        } else {
            // Fallback to window.print with targeted print styles
            reportContainer.style.position = 'static';
            reportContainer.style.left = '0';
            window.print();
        }

        document.body.removeChild(reportContainer);
    } catch (err) {
        alert('PDF Generation failed: ' + err.message);
    } finally {
        if (btn) {
            btn.innerHTML = originalText;
            btn.disabled = false;
        }
    }
}

window.exportExecutivePdfReport = exportExecutivePdfReport;
