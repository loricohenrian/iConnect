const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'dashboard.js'), 'utf8');
const start = source.indexOf('async function refreshLiveNetworkPanels()');
const end = source.indexOf('function initOverviewLiveMonitoring()', start);
assert(start >= 0 && end > start);

async function render(bandwidthData, connectedData = { total_connected: 11 }) {
    const elements = {
        'live-total-bandwidth': { textContent: '700.0 MB' },
        'live-active-users': { textContent: '11' },
        'live-network-meta': { textContent: 'Last update' },
    };
    const context = vm.createContext({
        document: { getElementById: id => elements[id] },
        fetchBandwidthUsageData: async () => bandwidthData,
        fetchConnectedUsersData: async () => connectedData,
    });
    vm.runInContext(source.slice(start, end), context);
    await vm.runInContext('refreshLiveNetworkPanels()', context);
    return elements;
}

(async () => {
    const valid = await render({ bandwidth_today_mb: 800, total_bandwidth_mb: 25 });
    assert.equal(valid['live-total-bandwidth'].textContent, '800.0 MB');
    const zero = await render({ bandwidth_today_mb: 0, total_bandwidth_mb: 500 });
    assert.equal(zero['live-total-bandwidth'].textContent, '0.0 MB');
    for (const data of [null, { total_bandwidth_mb: 0 },
        { bandwidth_today_mb: null }, { bandwidth_today_mb: 'invalid' }]) {
        const failed = await render(data);
        assert.equal(failed['live-total-bandwidth'].textContent, '700.0 MB');
    }
    const failedConnection = await render({ bandwidth_today_mb: 100 }, null);
    assert.equal(failedConnection['live-total-bandwidth'].textContent, '700.0 MB');
    console.log('Network overview: 7 browser-script cases passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
