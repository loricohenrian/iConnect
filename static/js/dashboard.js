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
function initPlanChart(canvasId, data) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return null;
    if (typeof Chart === 'undefined') {
        showChartUnavailable(ctx);
        return null;
    }

    const brandColors = ['#7b2d3b', '#991b1b', '#ea580c', '#d97706', '#6366f1', '#10b981', '#0ea5e9'];

    return new Chart(ctx, {
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

    // Use native browser download instead of fetch/blob to avoid some browser security warnings
    const url = `/reports/generate/?type=${type}&period=${period}&format=${format}`;
    window.location.href = url;

    // Reset button after a short delay assuming download started
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
        setText('sys-cpu-temp', data.cpu_temp || '—');

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
