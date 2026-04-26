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

    if (!hamburger) return;

    hamburger.addEventListener('click', () => {
        sidebar.classList.toggle('open');
        overlay.classList.toggle('active');
    });

    if (overlay) {
        overlay.addEventListener('click', () => {
            sidebar.classList.remove('open');
            overlay.classList.remove('active');
        });
    }
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
                borderColor: '#1A73E8',
                backgroundColor: 'rgba(26, 115, 232, 0.1)',
                fill: true,
                tension: 0.3,
                pointBackgroundColor: '#1A73E8',
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
function initPlanChart(canvasId, data) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return null;
    if (typeof Chart === 'undefined') {
        showChartUnavailable(ctx);
        return null;
    }

    return new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: data.labels || [],
            datasets: [{
                data: data.values || [],
                backgroundColor: ['#1A73E8', '#0EA5E9', '#06B6D4', '#10B981'],
                borderWidth: 0,
                spacing: 2,
            }]
        },
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
                        padding: 16,
                        usePointStyle: true,
                    }
                },
                tooltip: {
                    backgroundColor: '#1E293B',
                    padding: 12,
                    titleFont: { family: "'Inter', sans-serif", size: 13 },
                    bodyFont: { family: "'Inter', sans-serif", size: 13 },
                }
            }
        }
    });
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
// Dashboard Stats Refresh
// ============================================
async function refreshDashboardStats() {
    try {
        const response = await fetch('/api/dashboard/stats/');
        const data = await response.json();

        // Update stat cards
        updateStatValue('revenue-today', '₱' + Number(data.revenue_today).toLocaleString());
        updateStatValue('connected-users', data.total_connected);
        updateStatValue('bandwidth-today', data.bandwidth_today_mb + ' MB');
        updateStatValue('roi-progress', data.roi_percentage + '%');
        updateStatValue('sessions-today', data.sessions_today);

        // Update ROI progress bar
        updateROIProgress(data.roi_percentage);

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

function renderBandwidthUsersTable(users) {
    const tbody = document.getElementById('bandwidth-users-body');
    if (!tbody) return;

    if (!users || users.length === 0) {
        tbody.innerHTML = '<tr><td colspan="3" class="text-center text-muted">No active users</td></tr>';
        return;
    }

    tbody.innerHTML = users.map((u) => {
        const device = u.device_name || 'Unknown';
        const mac = u.mac_address || 'N/A';
        const used = Number(u.bandwidth_used_mb || 0).toFixed(1);
        return `
            <tr>
                <td>${device}</td>
                <td class="text-xs text-muted">${mac}</td>
                <td class="font-semibold">${used} MB</td>
            </tr>
        `;
    }).join('');
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
    renderBandwidthUsersTable(bandwidthData.users || []);
    metaEl.textContent = `Updated ${new Date().toLocaleTimeString()}`;
}

function initOverviewLiveMonitoring() {
    if (!document.getElementById('live-total-bandwidth')) {
        return;
    }

    refreshLiveNetworkPanels();
    setInterval(refreshLiveNetworkPanels, 10000);
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

    try {
        const response = await fetch(`/reports/generate/?type=${type}&period=${period}&format=${format}`);
        if (response.ok) {
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = `iconnect_${type}_report_${period}.${format}`;
            link.click();
            URL.revokeObjectURL(url);
        } else {
            alert('Failed to generate report. Please try again.');
        }
    } catch (err) {
        alert('Connection error. Please try again.');
    }

    btn.textContent = origText;
    btn.disabled = false;
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
        const data = await response.json();

        updateStatValue('sys-temp', data.cpu_temp || 'N/A');
        updateStatValue('sys-cpu', data.cpu_load || 'N/A');
        updateStatValue('sys-ram', data.ram_percent ? data.ram_percent + '%' : 'N/A');
        updateStatValue('sys-disk', data.disk_percent ? data.disk_percent + '%' : 'N/A');
    } catch (err) {
        console.error('Failed to refresh system stats:', err);
    }
}

// ============================================
// Initialize
// ============================================
document.addEventListener('DOMContentLoaded', () => {
    initSidebar();
    initOverviewLiveMonitoring();

    // Auto-refresh stats every 10 seconds (real-time)
    if (document.querySelector('.stats-grid')) {
        refreshDashboardStats();
        setInterval(refreshDashboardStats, 10000);

        // System stats
        refreshSystemStats();
        setInterval(refreshSystemStats, 10000);
    }
});
