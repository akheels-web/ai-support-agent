/**
 * National Finance AI Support Operations Dashboard - Chart.js Engine
 * Visualizes 24-hour volume trends, deflection rates, and queue distributions.
 */

let volumeTrendChart = null;
let deflectionDoughnutChart = null;
let queueDistChart = null;

async function initDashboardCharts() {
    const volumeCanvas = document.getElementById('volumeTrendChart');
    const deflectionCanvas = document.getElementById('deflectionDoughnutChart');
    const queueCanvas = document.getElementById('queueDistChart');

    if (!volumeCanvas && !deflectionCanvas && !queueCanvas) return;

    try {
        const resp = await fetch('/api/dashboard/chart-data');
        if (!resp.ok) return;
        const chartData = await resp.json();

        const isDark = document.documentElement.classList.contains('dark');
        const theme = getChartTheme(isDark);

        // 1. 24-Hour Volume & Deflection Trend
        if (volumeCanvas) {
            const ctx = volumeCanvas.getContext('2d');
            
            // Create smooth gradient fills
            const totalGradient = ctx.createLinearGradient(0, 0, 0, 300);
            totalGradient.addColorStop(0, 'rgba(27, 47, 107, 0.25)');
            totalGradient.addColorStop(1, 'rgba(27, 47, 107, 0.00)');

            const deflectedGradient = ctx.createLinearGradient(0, 0, 0, 300);
            deflectedGradient.addColorStop(0, 'rgba(16, 185, 129, 0.25)');
            deflectedGradient.addColorStop(1, 'rgba(16, 185, 129, 0.00)');

            volumeTrendChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: chartData.volume_trend.labels || [],
                    datasets: [
                        {
                            label: 'Total Calls',
                            data: chartData.volume_trend.total || [],
                            borderColor: '#1B2F6B',
                            backgroundColor: totalGradient,
                            borderWidth: 2.5,
                            fill: true,
                            tension: 0.35,
                            pointRadius: 3,
                            pointHoverRadius: 6,
                        },
                        {
                            label: 'AI Deflected / Resolved',
                            data: chartData.volume_trend.deflected || [],
                            borderColor: '#10B981',
                            backgroundColor: deflectedGradient,
                            borderWidth: 2.5,
                            fill: true,
                            tension: 0.35,
                            pointRadius: 3,
                            pointHoverRadius: 6,
                        },
                        {
                            label: 'Escalated / Transferred',
                            data: chartData.volume_trend.escalated || [],
                            borderColor: '#C8102E',
                            borderWidth: 2,
                            borderDash: [5, 5],
                            fill: false,
                            tension: 0.35,
                            pointRadius: 3,
                            pointHoverRadius: 6,
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: {
                        mode: 'index',
                        intersect: false,
                    },
                    plugins: {
                        legend: {
                            position: 'top',
                            labels: {
                                color: theme.textColor,
                                font: { family: 'Inter', size: 12, weight: '600' },
                                usePointStyle: true,
                                boxWidth: 8,
                            }
                        },
                        tooltip: {
                            backgroundColor: theme.tooltipBg,
                            titleColor: theme.tooltipText,
                            bodyColor: theme.tooltipText,
                            borderColor: theme.borderColor,
                            borderWidth: 1,
                            padding: 10,
                            boxPadding: 4,
                            usePointStyle: true,
                        }
                    },
                    scales: {
                        x: {
                            grid: { color: theme.gridColor, drawBorder: false },
                            ticks: { color: theme.mutedColor, font: { family: 'Inter', size: 11 } }
                        },
                        y: {
                            beginAtZero: true,
                            grid: { color: theme.gridColor, drawBorder: false },
                            ticks: { color: theme.mutedColor, font: { family: 'Inter', size: 11 }, precision: 0 }
                        }
                    }
                }
            });
        }

        // 2. Deflection & Resolution Breakdown (Doughnut)
        if (deflectionCanvas) {
            const ctx = deflectionCanvas.getContext('2d');
            deflectionDoughnutChart = new Chart(ctx, {
                type: 'doughnut',
                data: {
                    labels: ['AI First-Contact Resolved', 'Queue 7001 (L1 Support)', 'Queue 7002 (VIP Concierge)', 'Queue 7003 (Emergency Sev-1)', 'Ticket Logged'],
                    datasets: [{
                        data: chartData.deflection_breakdown || [0, 0, 0, 0, 0],
                        backgroundColor: ['#10B981', '#2B4A9F', '#F59E0B', '#C8102E', '#6B7280'],
                        borderWidth: 2,
                        borderColor: theme.cardBg,
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: '68%',
                    plugins: {
                        legend: {
                            position: 'bottom',
                            labels: {
                                color: theme.textColor,
                                font: { family: 'Inter', size: 11, weight: '500' },
                                boxWidth: 12,
                                padding: 12,
                            }
                        },
                        tooltip: {
                            backgroundColor: theme.tooltipBg,
                            titleColor: theme.tooltipText,
                            bodyColor: theme.tooltipText,
                            borderColor: theme.borderColor,
                            borderWidth: 1,
                            padding: 10,
                        }
                    }
                }
            });
        }

        // 3. Queue Distribution (Bar Chart)
        if (queueCanvas) {
            const ctx = queueCanvas.getContext('2d');
            queueDistChart = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: ['L1 IT Queue 7001', 'VIP Concierge 7002', 'Emergency Sev-1 7003'],
                    datasets: [{
                        label: 'Interactions Routed',
                        data: chartData.queue_distribution || [0, 0, 0],
                        backgroundColor: ['#2B4A9F', '#F59E0B', '#C8102E'],
                        borderRadius: 6,
                        borderSkipped: false,
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            backgroundColor: theme.tooltipBg,
                            titleColor: theme.tooltipText,
                            bodyColor: theme.tooltipText,
                        }
                    },
                    scales: {
                        x: {
                            grid: { display: false },
                            ticks: { color: theme.mutedColor, font: { family: 'Inter', size: 11 } }
                        },
                        y: {
                            beginAtZero: true,
                            grid: { color: theme.gridColor },
                            ticks: { color: theme.mutedColor, font: { family: 'Inter', size: 11 }, precision: 0 }
                        }
                    }
                }
            });
        }

    } catch (err) {
        console.warn('Charts init error:', err);
    }
}

function getChartTheme(isDark) {
    if (isDark) {
        return {
            textColor: '#F3F4F6',
            mutedColor: '#9CA3AF',
            gridColor: 'rgba(255, 255, 255, 0.08)',
            tooltipBg: '#111827',
            tooltipText: '#F9FAFB',
            borderColor: '#374151',
            cardBg: '#0a1020',
        };
    }
    return {
        textColor: '#1F2937',
        mutedColor: '#6B7280',
        gridColor: 'rgba(0, 0, 0, 0.06)',
        tooltipBg: '#FFFFFF',
        tooltipText: '#111827',
        borderColor: '#E5E7EB',
        cardBg: '#FFFFFF',
    };
}

function updateChartsTheme(isDark) {
    const theme = getChartTheme(isDark);

    [volumeTrendChart, deflectionDoughnutChart, queueDistChart].forEach(chart => {
        if (!chart) return;
        if (chart.options.plugins?.legend?.labels) {
            chart.options.plugins.legend.labels.color = theme.textColor;
        }
        if (chart.options.scales?.x) {
            chart.options.scales.x.ticks.color = theme.mutedColor;
            chart.options.scales.x.grid.color = theme.gridColor;
        }
        if (chart.options.scales?.y) {
            chart.options.scales.y.ticks.color = theme.mutedColor;
            chart.options.scales.y.grid.color = theme.gridColor;
        }
        chart.update();
    });
}

window.updateChartsTheme = updateChartsTheme;
document.addEventListener('DOMContentLoaded', initDashboardCharts);
