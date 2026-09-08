/**
 * iConnect — Admin Dashboard JavaScript
 * Charts, heatmap, real-time stats, sidebar toggle
 */

// ============================================
// Sidebar Toggle (Mobile)
// ============================================
function initSidebar() {
    const hamburger = document.getElementById('sidebar-toggle');
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebar-overlay');
    const closeBtn = document.getElementById('sidebar-close-btn');

    if (!sidebar) return;

    function closeSidebar() {
        sidebar.classList.remove('open');
        if (overlay) overlay.classList.remove('active');
        document.body.style.overflow = '';
    }

    function openSidebar() {
        sidebar.classList.add('open');
        if (overlay) overlay.classList.add('active');
        document.body.style.overflow = 'hidden';
    }

    if (hamburger) {
        hamburger.addEventListener('click', (e) => {
            e.stopPropagation();
            if (sidebar.classList.contains('open')) {
                closeSidebar();
            } else {
                openSidebar();
            }
        });
    }

    if (closeBtn) {
        closeBtn.addEventListener('click', closeSidebar);
    }

    if (overlay) {
        overlay.addEventListener('click', closeSidebar);
    }

    window.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && sidebar.classList.contains('open')) {
            closeSidebar();
        }
    });
}

// ============================================
// Revenue Chart (Chart.js)
// ============================================
function showChartUnavailable(canvasEl) {
    if (!canvasEl || !canvasEl.parentElement) return;
    if (canvasEl.parentElement.querySelector('.chart-unavailable-note')) return;

    const note = document.createElement('p');
    note.className = 'text-small text-muted chart-unavailable-note';
    note.textContent = 'Chart library unavailable. Data is still accessible through tables and stats.';
    canvasEl.parentElement.appendChild(note);
}

function initRevenueChart(canvasId, data) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return null;
    if (typeof Chart === 'undefined') {
        showChartUnavailable(ctx);
        return null;
    }

    return new Chart(ctx, {
        type: 'bar',
        data: {
            labels: data.labels || [],
            datasets: [{
                label: 'Revenue (₱)',
                data: data.values || [],
                backgroundColor: '#1A73E8',
                borderRadius: 6,
                barPercentage: 0.6,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: '#1E293B',
                    padding: 12,
                    titleFont: { family: "'Inter', sans-serif", size: 13 },
                    bodyFont: { family: "'Inter', sans-serif", size: 13 },
                    callbacks: {
                        label: (ctx) => `₱${ctx.parsed.y.toLocaleString()}`
                    }
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    grid: { color: '#E2E8F0' },
                    ticks: {
                        font: { family: "'Inter', sans-serif", size: 12 },
                        color: '#64748B',
                        callback: (val) => '₱' + val.toLocaleString()
                    }
                },
                x: {
                    grid: { display: false },
                    ticks: {
                        font: { family: "'Inter', sans-serif", size: 12 },
                        color: '#64748B'
                    }
                }
            }
        }
    });
}

// ============================================
// Sessions Chart (Line)
// ============================================
function initSessionsChart(canvasId, data) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return null;
    if (typeof Chart === 'undefined') {
        showChartUnavailable(ctx);
        return null;
    }

    return new Chart(ctx, {
        type: 'line',
        data: {
            labels: data.labels || [],
            datasets: [{
                label: 'Sessions',
                data: data.values || [],
                borderColor: '#7b2d3b',
                backgroundColor: 'rgba(123, 45, 59, 0.08)',
                fill: true,
                tension: 0.35,
                pointBackgroundColor: '#7b2d3b',
                pointBorderColor: '#ffffff',
                pointBorderWidth: 2,
                pointRadius: 4,
                pointHoverRadius: 6,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: '#1E293B',
                    padding: 12,
                    titleFont: { family: "'Inter', sans-serif", size: 13 },
                    bodyFont: { family: "'Inter', sans-serif", size: 13 },
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    grid: { color: '#E2E8F0' },
                    ticks: {
                        font: { family: "'Inter', sans-serif", size: 12 },
                        color: '#64748B'
                    }
                },
                x: {
                    grid: { display: false },
                    ticks: {
                        font: { family: "'Inter', sans-serif", size: 12 },
                        color: '#64748B'
                    }
                }
            }
        }
    });
}

// ============================================
// Plan Popularity (Doughnut)
// ============================================
let planChartInstance = null;
function initPlanChart(canvasId, data) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return null;
    if (typeof Chart === 'undefined') {
        showChartUnavailable(ctx);
        return null;
    }

    if (planChartInstance) {
        try { planChartInstance.destroy(); } catch (e) {}
        planChartInstance = null;
    }

    const brandColors = ['#7b2d3b', '#991b1b', '#ea580c', '#d97706', '#6366f1', '#10b981', '#0ea5e9'];

    // Custom inline plugin for segment percentages and center total
    const donutDataLabelsPlugin = {
        id: 'donutDataLabels',
        afterDraw(chart) {
            const { ctx, chartArea } = chart;
            if (!chartArea) return;
            const meta = chart.getDatasetMeta(0);
            if (!meta || !meta.data || !meta.data.length) return;

            const dataset = chart.data.datasets[0];
            const total = (dataset.data || []).reduce((acc, val) => acc + Number(val || 0), 0);
            if (total <= 0) return;

            ctx.save();

            // 1. Center summary text
            const centerX = (chartArea.left + chartArea.right) / 2;
            const centerY = (chartArea.top + chartArea.bottom) / 2;

            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';

            ctx.font = "bold 22px 'Inter', sans-serif";
            ctx.fillStyle = '#1E293B';
            ctx.fillText(total.toLocaleString(), centerX, centerY - 9);

            ctx.font = "500 11px 'Inter', sans-serif";
            ctx.fillStyle = '#64748B';
            ctx.fillText('Total Sessions', centerX, centerY + 13);

            // 2. Direct on-segment percentage labels
            meta.data.forEach((element, i) => {
                if (!chart.getDataVisibility(i)) return;
                const val = Number(dataset.data[i] || 0);
                if (val <= 0) return;

                const percentage = (val / total) * 100;
                // Render on slice if segment is >= 6% to prevent clipping
                if (percentage < 6) return;

                const pos = element.tooltipPosition();
                if (!pos || isNaN(pos.x) || isNaN(pos.y)) return;

                ctx.font = "bold 11px 'Inter', sans-serif";
                ctx.fillStyle = '#ffffff';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.shadowColor = 'rgba(0, 0, 0, 0.45)';
                ctx.shadowBlur = 4;
                ctx.fillText(`${percentage.toFixed(0)}%`, pos.x, pos.y);
                ctx.shadowColor = 'transparent';
                ctx.shadowBlur = 0;
            });

            ctx.restore();
        }
    };

    planChartInstance = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: data.labels || [],
            datasets: [{
                data: data.values || [],
                backgroundColor: brandColors.slice(0, (data.values || []).length || 1),
                borderWidth: 0,
                spacing: 2,
            }]
        },
        plugins: [donutDataLabelsPlugin],
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '70%',
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: {
                        font: { family: "'Inter', sans-serif", size: 12 },
                        color: '#64748B',
                        padding: 14,
                        usePointStyle: true,
                        generateLabels: function(chart) {
                            const chartData = chart.data;
                            if (chartData.labels.length && chartData.datasets.length) {
                                const dataset = chartData.datasets[0];
                                const total = dataset.data.reduce((acc, val) => acc + Number(val || 0), 0);
                                return chartData.labels.map((label, i) => {
                                    const value = Number(dataset.data[i] || 0);
                                    const percentage = total > 0 ? ((value / total) * 100).toFixed(1) : '0';
                                    const fill = dataset.backgroundColor[i] || '#64748B';
                                    return {
                                        text: `${label}: ${value} (${percentage}%)`,
                                        fillStyle: fill,
                                        strokeStyle: fill,
                                        lineWidth: 0,
                                        pointStyle: 'circle',
                                        hidden: !chart.getDataVisibility(i),
                                        index: i
                                    };
                                });
                            }
                            return [];
                        }
                    }
                },
                tooltip: {
                    backgroundColor: '#1E293B',
                    padding: 12,
                    cornerRadius: 8,
                    titleFont: { family: "'Inter', sans-serif", size: 13, weight: '600' },
                    bodyFont: { family: "'Inter', sans-serif", size: 13 },
                    callbacks: {
                        label: function(context) {
                            const label = context.label || '';
                            const value = Number(context.raw || 0);
                            const total = context.dataset.data.reduce((acc, curr) => acc + Number(curr || 0), 0);
                            const percentage = total > 0 ? ((value / total) * 100).toFixed(1) : '0';
                            return ` ${label}: ${value} session${value === 1 ? '' : 's'} (${percentage}%)`;
                        }
                    }
                }
            }
        }
    });

    return planChartInstance;
}

// ============================================
// Peak Hours Heatmap
// ============================================
function initHeatmap(containerId, data) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    const maxVal = Math.max(...data.map(d => d.count), 1);

    let html = '';

    // Hour labels row
    html += '<div class="heatmap-label"></div>';
    for (let h = 0; h < 24; h++) {
        html += `<div class="heatmap-hour-label">${h}</div>`;
    }

    // Data rows
    for (let d = 1; d <= 7; d++) {
        html += `<div class="heatmap-label">${days[d - 1]}</div>`;
        for (let h = 0; h < 24; h++) {
            const entry = data.find(item => item.weekday === d && item.hour === h);
            const count = entry ? entry.count : 0;
            const level = getHeatLevel(count, maxVal);
            html += `<div class="heatmap-cell heat-${level}" title="${days[d-1]} ${h}:00 — ${count} sessions">${count || ''}</div>`;
        }
    }

    container.innerHTML = html;
}

function getHeatLevel(value, max) {
    if (value === 0) return 0;
    const ratio = value / max;
    if (ratio <= 0.25) return 1;
    if (ratio <= 0.5) return 2;
    if (ratio <= 0.75) return 3;
    return 4;
}

// ============================================
// ROI Progress
// ============================================
function updateROIProgress(percentage) {
    const fill = document.querySelector('.progress-fill');
    if (fill) {
        fill.style.width = Math.min(percentage, 100) + '%';
    }
}

// ============================================
// HTML Escaping Utility
// ============================================
function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

// ============================================
// Recent Sessions Dynamic Table Renderer
// ============================================
function renderRecentSessions(sessions) {
    let table = document.getElementById('recent-sessions-table');
    const container = document.getElementById('recent-sessions-container');

    if (!Array.isArray(sessions)) return;

    if (!table && container && sessions.length > 0) {
        container.innerHTML = `
        <div class="table-container">
            <table id="recent-sessions-table">
                <thead>
                    <tr>
                        <th>Hostname / Device</th>
                        <th>Plan</th>
                        <th>Amount</th>
                        <th>Time In</th>
                        <th>Remaining</th>
                        <th>Status</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        </div>`;
        table = document.getElementById('recent-sessions-table');
    }

    if (!table) return;

    const tbody = table.querySelector('tbody');
    if (!tbody) return;

    if (sessions.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted" style="padding: 24px;">No recent sessions found</td></tr>';
        return;
    }

    const rowsHtml = sessions.map(s => {
        let badgeHtml = '';
        if (s.status === 'active') {
            badgeHtml = '<span class="badge badge-active">Active</span>';
        } else if (s.status === 'expired') {
            badgeHtml = '<span class="badge badge-expired">Expired</span>';
        } else {
            badgeHtml = `<span class="badge badge-paused">${escapeHtml(s.status_display || s.status)}</span>`;
        }

        const countdownClass = s.status === 'active' ? 'countdown-cell' : '';
        const timeRemainingText = s.status === 'expired' ? '00:00:00' : escapeHtml(s.time_remaining_display || '00:00:00');

        return `<tr>
            <td>
                <div class="font-semibold">${escapeHtml(s.device_name || 'Unknown')}</div>
                <div class="text-xs text-muted font-mono">${escapeHtml(s.mac_address)}</div>
            </td>
            <td>
                <span class="badge badge-info">${escapeHtml(s.plan_name || 'Custom')}</span>
            </td>
            <td class="font-semibold">₱${escapeHtml(s.amount_paid)}</td>
            <td class="text-xs text-muted">${escapeHtml(s.time_in)}</td>
            <td class="text-xs font-mono ${countdownClass}" data-remaining="${s.time_remaining_seconds}" data-status="${escapeHtml(s.status)}">
                ${timeRemainingText}
            </td>
            <td>
                ${badgeHtml}
            </td>
        </tr>`;
    }).join('');

    tbody.innerHTML = rowsHtml;
}

// ============================================
// Dashboard Stats Refresh
// ============================================
async function refreshDashboardStats() {
    try {
        const response = await fetch('/api/dashboard/stats/');
        if (!response.ok) return;
        const data = await response.json();

        // Update stat cards (preserve currency symbol styling if present)
        if (document.getElementById('revenue-today-val')) {
            updateStatValue('revenue-today-val', Number(data.revenue_today || 0).toLocaleString());
        } else {
            updateStatValue('revenue-today', '₱' + Number(data.revenue_today || 0).toLocaleString());
        }

        if (data.revenue_this_week !== undefined) {
            updateStatValue('revenue-this-week-val', Number(data.revenue_this_week || 0).toLocaleString());
        }
        if (data.revenue_this_month !== undefined) {
            updateStatValue('revenue-this-month-val', Number(data.revenue_this_month || 0).toLocaleString());
        }

        updateStatValue('connected-users', data.total_connected || 0);
        updateStatValue('bandwidth-today', (data.bandwidth_today_mb || 0) + ' MB');
        updateStatValue('sessions-today', data.sessions_today || 0);

        // Update live network panel in overview
        updateStatValue('live-active-users', data.total_connected || 0);
        if (data.bandwidth_today_mb !== undefined) {
            updateStatValue('live-total-bandwidth', (data.bandwidth_today_mb || 0) + ' MB');
        }
        const liveMeta = document.getElementById('live-network-meta');
        if (liveMeta) {
            liveMeta.textContent = `Updated ${new Date().toLocaleTimeString()}`;
        }

        // Update ROI progress value and color
        const roiProgressEl = document.getElementById('roi-progress');
        if (roiProgressEl && data.roi_percentage !== undefined) {
            const roiVal = parseFloat(data.roi_percentage) || 0;
            roiProgressEl.textContent = (roiVal > 0 ? '+' : '') + roiVal.toFixed(1) + '%';
            if (roiVal > 0) {
                roiProgressEl.style.color = 'var(--color-success)';
            } else if (roiVal < 0) {
                roiProgressEl.style.color = 'var(--color-danger)';
            } else {
                roiProgressEl.style.color = 'var(--color-dark)';
            }
        }

        // Update ROI progress bar fill if present
        if (data.roi_percentage !== undefined) {
            updateROIProgress(parseFloat(data.roi_percentage) || 0);
        }

        // Update recent sessions table in real time
        if (data.recent_sessions) {
            renderRecentSessions(data.recent_sessions);
        }

        return data;
    } catch (err) {
        console.error('Failed to refresh stats:', err);
    }
}

function updateStatValue(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
}

// ============================================
// Revenue Data Fetch
// ============================================
async function fetchRevenueData(period = 'weekly') {
    try {
        const response = await fetch(`/api/dashboard/revenue/?period=${period}`);
        return await response.json();
    } catch (err) {
        console.error('Failed to fetch revenue data:', err);
        return null;
    }
}

// ============================================
// Heatmap Data Fetch
// ============================================
async function fetchHeatmapData() {
    try {
        const response = await fetch('/api/dashboard/heatmap/');
        const data = await response.json();
        return data.heatmap || [];
    } catch (err) {
        console.error('Failed to fetch heatmap data:', err);
        return [];
    }
}

// ============================================
// Live Network Monitoring (Overview)
// ============================================
async function fetchBandwidthUsageData() {
    try {
        const response = await fetch('/api/bandwidth/');
        return await response.json();
    } catch (err) {
        console.error('Failed to fetch bandwidth usage:', err);
        return null;
    }
}

async function fetchConnectedUsersData() {
    try {
        const response = await fetch('/api/connected-users/');
        return await response.json();
    } catch (err) {
        console.error('Failed to fetch connected users:', err);
        return null;
    }
}


async function refreshLiveNetworkPanels() {
    const totalBandwidthEl = document.getElementById('live-total-bandwidth');
    const activeUsersEl = document.getElementById('live-active-users');
    const metaEl = document.getElementById('live-network-meta');
    if (!totalBandwidthEl || !activeUsersEl || !metaEl) return;

    const [bandwidthData, connectedData] = await Promise.all([
        fetchBandwidthUsageData(),
        fetchConnectedUsersData(),
    ]);

    if (!bandwidthData || !connectedData) {
        metaEl.textContent = 'Failed to refresh live network data.';
        return;
    }

    totalBandwidthEl.textContent = `${Number(bandwidthData.total_bandwidth_mb || 0).toFixed(1)} MB`;
    activeUsersEl.textContent = connectedData.total_connected || 0;
    metaEl.textContent = `Updated ${new Date().toLocaleTimeString()}`;
}

function initOverviewLiveMonitoring() {
    if (!document.getElementById('live-total-bandwidth')) {
        return;
    }

    // Only run dedicated polling if main dashboard stats poll is not already active
    const hasOverviewMainPoll = document.getElementById('revenue-today') || 
                                document.getElementById('revenue-today-val') || 
                                document.querySelector('.dashboard-hero-layout');
    if (!hasOverviewMainPoll) {
        refreshLiveNetworkPanels();
        setInterval(refreshLiveNetworkPanels, 10000);
    }
}

// ============================================
// Export CSV
// ============================================
function exportTableToCSV(tableId, filename) {
    const table = document.getElementById(tableId);
    if (!table) return;

    let csv = [];
    const rows = table.querySelectorAll('tr');

    rows.forEach(row => {
        const cols = row.querySelectorAll('td, th');
        const rowData = [];
        cols.forEach(col => {
            rowData.push('"' + col.textContent.trim().replace(/"/g, '""') + '"');
        });
        csv.push(rowData.join(','));
    });

    const blob = new Blob([csv.join('\n')], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename || 'export.csv';
    link.click();
    URL.revokeObjectURL(url);
}

// ============================================
// Report Generation
// ============================================
async function generateReport(button, type, period, format = 'pdf') {
    const btn = button;
    const origText = btn.textContent;
    btn.innerHTML = '<span class="spinner"></span> Generating...';
    btn.disabled = true;

    // Use native browser download instead of fetch/blob to avoid some browser security warnings
    const url = `/reports/generate/?type=${type}&period=${period}&format=${format}`;
    window.location.href = url;

    // Reset button after a short delay assuming download started
    setTimeout(() => {
        btn.textContent = origText;
        btn.disabled = false;
    }, 2000);
}

async function generateCustomReport(button, format = 'pdf') {
    const startInput = document.getElementById('custom-report-start');
    const endInput = document.getElementById('custom-report-end');
    const startDate = startInput ? startInput.value.trim() : '';
    const endDate = endInput ? endInput.value.trim() : '';

    if (!startDate && !endDate) {
        alert('Please select at least a Start Date or End Date for the custom report.');
        return;
    }

    const btn = button;
    const origText = btn.textContent;
    btn.innerHTML = '<span class="spinner"></span> Generating...';
    btn.disabled = true;

    const url = `/reports/generate/?type=custom&period=custom&format=${format}&start_date=${encodeURIComponent(startDate)}&end_date=${encodeURIComponent(endDate)}`;
    window.location.href = url;

    setTimeout(() => {
        btn.textContent = origText;
        btn.disabled = false;
    }, 2000);
}

// ============================================
// Utility
// ============================================
function formatPeso(amount) {
    return '₱' + Number(amount).toLocaleString();
}

function getCSRFToken() {
    const cookie = document.cookie.split(';').find(c => c.trim().startsWith('csrftoken='));
    return cookie ? cookie.split('=')[1] : '';
}

// ============================================
// System Stats (CPU, RAM, Disk, Temp)
// ============================================
async function refreshSystemStats() {
    try {
        const response = await fetch('/api/dashboard/system/');
        if (!response.ok) return;
        const data = await response.json();

        // --- Disk ---
        const diskPct = data.disk_percent || 0;
        setText('sys-disk-total', data.disk_total || '—');
        setText('sys-disk-used', data.disk_used || '—');
        setText('sys-disk-pct', diskPct + '%');
        setBar('sys-disk-bar', diskPct);

        // --- CPU ---
        const cpuPct = data.cpu_load_raw || 0;
        setText('sys-cpu-count', data.cpu_count || '—');
        setText('sys-cpu-pct', cpuPct.toFixed(1) + '%');
        setBar('sys-cpu-bar', cpuPct);

        // --- RAM ---
        const ramPct = data.ram_percent || 0;
        setText('sys-ram-total', data.ram_total || '—');
        setText('sys-ram-used', data.ram_used || '—');
        setText('sys-ram-pct', ramPct + '%');
        setBar('sys-ram-bar', ramPct);

        // --- Internet Status ---
        const internetDotEl = document.getElementById('sys-internet-dot');
        const internetTextEl = document.getElementById('sys-internet-text');
        if (internetDotEl && internetTextEl) {
            if (data.internet_online === true) {
                internetDotEl.style.background = '#10B981'; // Green
                internetDotEl.style.boxShadow = '0 0 8px rgba(16, 185, 129, 0.5)';
                internetTextEl.textContent = 'Internet is up';
            } else if (data.internet_online === false) {
                internetDotEl.style.background = '#EF4444'; // Red
                internetDotEl.style.boxShadow = '0 0 8px rgba(239, 68, 68, 0.5)';
                internetTextEl.textContent = 'Internet is down';
            } else {
                internetDotEl.style.background = '#94A3B8';
                internetDotEl.style.boxShadow = 'none';
                internetTextEl.textContent = 'Unknown';
            }
        }
        
        // --- CPU Temp ---
        const tempEl = document.getElementById('sys-cpu-temp');
        if (tempEl) {
            tempEl.textContent = data.cpu_temp || '—';
            const tempVal = Number(data.cpu_temp_val);
            if (!isNaN(tempVal) && data.cpu_temp_val !== null) {
                if (tempVal >= 80) {
                    tempEl.style.color = '#EF4444'; // Critical (>80°C)
                } else if (tempVal >= 70) {
                    tempEl.style.color = '#F59E0B'; // Warning (>70°C)
                } else {
                    tempEl.style.color = 'var(--color-dark)'; // Normal
                }
            }
        }

    } catch (err) {
        console.error('Failed to refresh system stats:', err);
    }
}

function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
}

function setBar(id, pct) {
    const el = document.getElementById(id);
    if (el) {
        el.style.width = Math.min(100, Math.max(0, pct)) + '%';
        
        // Remove existing semantic classes
        el.classList.remove('progress-success', 'progress-warning', 'progress-danger');
        
        // Add threshold class
        if (pct > 85) {
            el.classList.add('progress-danger');
        } else if (pct >= 60) {
            el.classList.add('progress-warning');
        } else {
            el.classList.add('progress-success');
        }
    }
}

// ============================================
// Real-time Revenue Page Monitor
// ============================================
let revenueChartInstance = null;

async function refreshRevenueLive() {
    try {
        const queryParams = window.location.search;
        const response = await fetch(`/api/dashboard/revenue/live/${queryParams}`);
        if (!response.ok) return;
        const data = await response.json();

        // Update Top Metric Cards
        const totalSalesEl = document.getElementById('revenue-total-sales');
        if (totalSalesEl) totalSalesEl.textContent = '₱' + Number(data.total_sales).toLocaleString();

        const totalSessionsEl = document.getElementById('revenue-total-sessions');
        if (totalSessionsEl) {
            let pendingNotice = (data.total_sessions > 0 && data.total_sales === 0) 
                ? ' <span style="font-size: 11px; color: var(--color-warning); font-weight: 600; text-transform: uppercase;">(Pending)</span>' 
                : '';
            totalSessionsEl.innerHTML = `<span>${data.total_sessions}</span>${pendingNotice}`;
        }

        const avgTransEl = document.getElementById('revenue-avg-transaction');
        if (avgTransEl) avgTransEl.textContent = '₱' + Number(data.avg_transaction).toLocaleString();

        // Update Chart if present
        const chartCanvas = document.getElementById('plan-sales-chart');
        if (chartCanvas && typeof Chart !== 'undefined') {
            if (!revenueChartInstance) {
                revenueChartInstance = Chart.getChart(chartCanvas);
            }
            if (revenueChartInstance && data.plan_labels && data.plan_data) {
                revenueChartInstance.data.labels = data.plan_labels;
                revenueChartInstance.data.datasets[0].data = data.plan_data;
                revenueChartInstance.update('none'); // Update without flickering animation
            }
        }

        // Update Revenue Targets if returned
        if (data.today_sales_for_goal !== undefined && data.daily_target_amt !== undefined) {
            const goalTodayText = document.getElementById('goal-today-text');
            const goalTodayPct = document.getElementById('goal-today-pct');
            const goalTodayBar = document.getElementById('goal-today-bar');

            if (goalTodayText) {
                goalTodayText.textContent = `Today's Target: ₱${Number(data.today_sales_for_goal).toLocaleString()} / ₱${Number(data.daily_target_amt).toLocaleString()}`;
            }
            if (goalTodayPct) {
                goalTodayPct.textContent = `${data.daily_progress}%`;
                goalTodayPct.style.color = data.daily_progress >= 100 ? '#059669' : '#2563eb';
            }
            if (goalTodayBar) {
                goalTodayBar.style.width = `${Math.min(100, data.daily_progress)}%`;
                goalTodayBar.style.background = data.daily_progress >= 100 ? '#059669' : '#2563eb';
            }
        }

        if (data.week_sales_for_goal !== undefined && data.weekly_target_amt !== undefined) {
            const goalWeekText = document.getElementById('goal-week-text');
            const goalWeekPct = document.getElementById('goal-week-pct');
            const goalWeekBar = document.getElementById('goal-week-bar');

            if (goalWeekText) {
                goalWeekText.textContent = `This Week's Target: ₱${Number(data.week_sales_for_goal).toLocaleString()} / ₱${Number(data.weekly_target_amt).toLocaleString()}`;
            }
            if (goalWeekPct) {
                goalWeekPct.textContent = `${data.weekly_progress}%`;
                goalWeekPct.style.color = data.weekly_progress >= 100 ? '#059669' : '#7c3aed';
            }
            if (goalWeekBar) {
                goalWeekBar.style.width = `${Math.min(100, data.weekly_progress)}%`;
                goalWeekBar.style.background = data.weekly_progress >= 100 ? '#059669' : '#7c3aed';
            }
        }

        // Update Filtered Sessions Table
        const tbody = document.getElementById('revenue-sessions-tbody');
        const container = document.getElementById('revenue-table-container');
        const pagWrapper = document.getElementById('revenue-pagination-wrapper');
        const pagInfo = document.getElementById('revenue-pagination-info');
        const emptyState = document.getElementById('revenue-empty-state');

        if (tbody && Array.isArray(data.sessions)) {
            if (data.sessions.length === 0) {
                if (container) container.style.display = 'none';
                if (pagWrapper) pagWrapper.style.display = 'none';
                if (emptyState) emptyState.style.display = 'block';
                tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted" style="padding: 24px;">No sessions found for this date range.</td></tr>';
            } else {
                if (container) container.style.display = '';
                if (pagWrapper) pagWrapper.style.display = 'flex';
                if (emptyState) emptyState.style.display = 'none';
                if (pagInfo) {
                    pagInfo.textContent = `Showing ${data.start_index} to ${data.end_index} of ${data.total_count} sessions`;
                }

                tbody.innerHTML = data.sessions.map(s => {
                    let badgeHtml = '';
                    if (s.status === 'active') {
                        badgeHtml = '<span class="badge badge-active">Active</span>';
                    } else if (s.status === 'expired') {
                        badgeHtml = '<span class="badge badge-expired">Expired</span>';
                    } else {
                        badgeHtml = `<span class="badge badge-paused">${escapeHtml(s.status_display || s.status)}</span>`;
                    }

                    return `<tr>
                        <td class="text-xs text-muted">#${s.id}</td>
                        <td>
                            <div class="font-semibold">${escapeHtml(s.device_name || 'Unknown')}</div>
                            <div class="font-mono text-xs text-muted">${escapeHtml(s.mac_address)}</div>
                        </td>
                        <td>
                            <span class="badge badge-info">${escapeHtml(s.plan_name || 'Custom')}</span>
                        </td>
                        <td>
                            <div>${escapeHtml(s.time_in_date)}</div>
                            <div class="text-xs text-muted">${escapeHtml(s.time_in_time)}</div>
                        </td>
                        <td class="font-mono text-xs">${escapeHtml(s.ip_address)}</td>
                        <td>${s.duration_minutes_purchased}m</td>
                        <td class="font-semibold">₱${escapeHtml(s.amount_paid)}</td>
                        <td>${badgeHtml}</td>
                    </tr>`;
                }).join('');
            }
        }
    } catch (err) {
        console.error('Failed to refresh live revenue:', err);
    }
}

function initRevenueLiveMonitoring() {
    if (document.getElementById('revenue-total-sales') || document.getElementById('revenue-sessions-table')) {
        refreshRevenueLive();
        setInterval(refreshRevenueLive, 3000);
    }
}

// ============================================
// Real-time Sessions / Users Page Monitor
// ============================================
async function refreshSessionsLive() {
    try {
        const queryParams = window.location.search;
        const response = await fetch(`/api/dashboard/sessions/live/${queryParams}`);
        if (!response.ok) return;
        const data = await response.json();

        // Update Top Stat Cards
        setText('sessions-total-users', data.total_users || 0);
        setText('sessions-connected-users', data.connected_users || 0);
        setText('sessions-paused-users', data.paused_users || 0);
        setText('sessions-expired-users', data.disconnected_users || 0);

        // Update Sessions Table
        const tbody = document.getElementById('sessions-tbody');
        if (tbody && Array.isArray(data.sessions)) {
            if (data.sessions.length === 0) {
                tbody.innerHTML = '<tr><td colspan="13" class="text-center text-muted" style="padding: 24px;">No sessions found matching filters</td></tr>';
                return;
            }

            tbody.innerHTML = data.sessions.map(s => {
                let badgeHtml = '';
                if (s.status === 'active') {
                    badgeHtml = '<span class="badge badge-active">Active</span>';
                } else if (s.status === 'expired') {
                    badgeHtml = '<span class="badge badge-expired">Expired</span>';
                } else {
                    badgeHtml = `<span class="badge badge-paused">${escapeHtml(s.status_display || s.status)}</span>`;
                }

                const countdownClass = s.status === 'active' ? 'countdown-cell' : '';
                const timeRemainingText = s.status === 'expired' ? '00:00:00' : escapeHtml(s.time_remaining_display || '00:00:00');

                const alertBadge = s.is_suspicious 
                    ? `<a href="/iconnect-ops/security/?status=all&q=${encodeURIComponent(s.mac_address)}" class="badge" style="background: #fee2e2; color: #dc2626; font-size: 10px; padding: 1px 5px; border-radius: 4px; font-weight: 700; text-decoration: none;" title="Flagged for Suspicious Activity — View in Security">⚠️ Alert</a>` 
                    : '';

                const groupHtml = s.group_code 
                    ? `<div class="text-xs" style="color: #6366F1; margin-top: 2px;">Group: ${escapeHtml(s.group_code)}</div>` 
                    : '';

                let actionsHtml = '';
                if (s.status === 'active') {
                    actionsHtml += `<button class="action-btn action-btn-pause" title="Pause Session" 
                        data-session-id="${s.id}" data-action="pause" data-device-name="${escapeHtml(s.device_name || '')}" data-mac="${escapeHtml(s.mac_address || '')}"
                        onclick="openActionModal(this.dataset.sessionId, this.dataset.action, this.dataset.deviceName, this.dataset.mac)">
                        <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor"><path d="M5.5 3.5A1.5 1.5 0 0 1 7 5v6a1.5 1.5 0 0 1-3 0V5a1.5 1.5 0 0 1 1.5-1.5zm5 0A1.5 1.5 0 0 1 12 5v6a1.5 1.5 0 0 1-3 0V5a1.5 1.5 0 0 1 1.5-1.5z"/></svg>
                    </button>`;
                } else if (s.status === 'paused') {
                    actionsHtml += `<button class="action-btn action-btn-resume" title="Resume Session" 
                        data-session-id="${s.id}" data-action="resume" data-device-name="${escapeHtml(s.device_name || '')}" data-mac="${escapeHtml(s.mac_address || '')}"
                        onclick="openActionModal(this.dataset.sessionId, this.dataset.action, this.dataset.deviceName, this.dataset.mac)">
                        <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor"><path d="m11.596 8.697-6.363 3.692c-.54.313-1.233-.066-1.233-.697V4.308c0-.63.692-1.01 1.233-.696l6.363 3.692a.802.802 0 0 1 0 1.393z"/></svg>
                    </button>`;
                }

                actionsHtml += `<button class="action-btn action-btn-add-time" title="Add Time (+Mins)" 
                    data-session-id="${s.id}" data-action="add_time" data-device-name="${escapeHtml(s.device_name || '')}" data-mac="${escapeHtml(s.mac_address || '')}"
                    data-plan-id="${s.plan_id || ''}"
                    data-speed-limit="${s.speed_limit !== null && s.speed_limit !== undefined ? s.speed_limit : ''}"
                    data-speed-limit-upload="${s.speed_limit_upload !== null && s.speed_limit_upload !== undefined ? s.speed_limit_upload : ''}"
                    onclick="openActionModal(this.dataset.sessionId, this.dataset.action, this.dataset.deviceName, this.dataset.mac, this.dataset.planId, this.dataset.speedLimit, this.dataset.speedLimitUpload)">
                    <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor"><path d="M8.5 5.5a.5.5 0 0 0-1 0v2.5H5a.5.5 0 0 0 0 1h2.5V11.5a.5.5 0 0 0 1 0V9H11a.5.5 0 0 0 0-1H8.5z"/><path d="M8 15A7 7 0 1 1 8 1a7 7 0 0 1 0 14m0 1A8 8 0 1 0 8 0a8 8 0 0 0 0 16"/></svg>
                </button>`;

                actionsHtml += `<button class="action-btn action-btn-edit" title="Edit Hostname" 
                    data-session-id="${s.id}" data-action="edit" data-device-name="${escapeHtml(s.device_name || '')}" data-mac="${escapeHtml(s.mac_address || '')}"
                    onclick="openActionModal(this.dataset.sessionId, this.dataset.action, this.dataset.deviceName, this.dataset.mac)">
                    <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor"><path d="M12.854.146a.5.5 0 0 0-.707 0L10.5 1.793 14.207 5.5l1.647-1.646a.5.5 0 0 0 0-.708zm.646 6.061L9.793 2.5 3.293 9H3.5a.5.5 0 0 1 .5.5v.5h.5a.5.5 0 0 1 .5.5v.5h.5a.5.5 0 0 1 .5.5v.5h.5a.5.5 0 0 1 .5.5v.207zm-7.468 7.468A.5.5 0 0 1 6 13.5V13h-.5a.5.5 0 0 1-.5-.5V12h-.5a.5.5 0 0 1-.5-.5V11h-.5a.5.5 0 0 1-.5-.5V10h-.5a.5.5 0 0 1-.175-.032l-.179.178a.5.5 0 0 0-.11.168l-2 5a.5.5 0 0 0 .65.65l5-2a.5.5 0 0 0 .168-.11z"/></svg>
                </button>`;

                if (s.status === 'active' || s.status === 'paused') {
                    actionsHtml += `<button class="action-btn action-btn-disconnect" title="End Session" 
                        data-session-id="${s.id}" data-action="disconnect" data-device-name="${escapeHtml(s.device_name || '')}" data-mac="${escapeHtml(s.mac_address || '')}"
                        onclick="openActionModal(this.dataset.sessionId, this.dataset.action, this.dataset.deviceName, this.dataset.mac)">
                        <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor"><path d="M5 3.5h6A1.5 1.5 0 0 1 12.5 5v6a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 11V5A1.5 1.5 0 0 1 5 3.5z"/></svg>
                    </button>`;
                }

                return `<tr>
                    <td>#${s.id}</td>
                    <td title="${escapeHtml(s.device_name)}" style="max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                        <div class="font-semibold d-flex align-items-center gap-xs">
                            <span>${escapeHtml(s.device_name || 'Unknown')}</span>
                            ${alertBadge}
                        </div>
                        ${groupHtml}
                    </td>
                    <td>
                        <span class="badge" style="background: rgba(123, 45, 59, 0.08); color: var(--color-dark, #7b2d3b); font-weight: 600;">
                            ${escapeHtml(s.plan_name || 'Custom')}
                        </span>
                    </td>
                    <td class="font-semibold" style="color: var(--color-dark);">₱${escapeHtml(s.amount_paid)}</td>
                    <td class="text-xs font-mono">${escapeHtml(s.ip_address)}</td>
                    <td class="font-mono text-xs text-muted">${escapeHtml(s.mac_address)}</td>
                    <td class="text-xs">${Number(s.bandwidth_used_mb || 0).toFixed(1)} MB</td>
                    <td class="text-xs font-mono ${countdownClass}" data-remaining="${s.time_remaining_seconds}" data-status="${escapeHtml(s.status)}">
                        ${timeRemainingText}
                    </td>
                    <td>${badgeHtml}</td>
                    <td class="text-xs">${escapeHtml(s.time_in)}</td>
                    <td class="text-xs">
                        ${escapeHtml(s.time_out || '—')}
                        ${s.is_ended_early ? `<span class="badge badge-expired" style="font-size: 0.65rem; padding: 1px 4px; margin-left: 4px; background: rgba(239, 68, 68, 0.12); color: var(--color-danger); border: 1px solid rgba(239, 68, 68, 0.25);" title="Ended early (ran ${s.actual_elapsed_minutes}m of ${s.duration_minutes_purchased}m purchased)">Early</span>` : ''}
                    </td>
                    <td class="text-xs">
                        ${s.duration_minutes_purchased}m
                        ${s.is_ended_early ? `<div class="text-muted" style="font-size: 0.7rem;">(ran ${s.actual_elapsed_minutes}m)</div>` : ''}
                    </td>
                    <td style="text-align: right;">
                        <div class="d-flex gap-xs" style="justify-content: flex-end;">
                            ${actionsHtml}
                        </div>
                    </td>
                </tr>`;
            }).join('');
        }
    } catch (err) {
        console.error('Failed to refresh live sessions:', err);
    }
}

function initSessionsLiveMonitoring() {
    if (document.getElementById('sessions-table') || document.getElementById('sessions-connected-users')) {
        refreshSessionsLive();
        setInterval(refreshSessionsLive, 3000);
    }
}

// ============================================
// Initialize
// ============================================
document.addEventListener('DOMContentLoaded', () => {
    initSidebar();
    initOverviewLiveMonitoring();
    initRevenueLiveMonitoring();
    initSessionsLiveMonitoring();

    // Auto-refresh stats on Overview / Dashboard every 3s
    if (document.getElementById('revenue-today') || document.getElementById('revenue-today-val') || document.querySelector('.dashboard-hero-layout') || document.querySelector('.sys-strip-container') || document.querySelector('.stats-grid')) {
        refreshDashboardStats();
        setInterval(refreshDashboardStats, 3000);

        // System stats (CPU, RAM, Disk, Temp, Internet)
        refreshSystemStats();
        setInterval(refreshSystemStats, 5000);
    }
});
