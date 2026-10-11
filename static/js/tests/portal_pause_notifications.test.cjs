const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'portal.js'), 'utf8');
const start = source.indexOf('// Status polling cannot identify who paused a session.');
const end = source.indexOf('// Real-time synchronization of Outage / Pause / Resume', start);
assert(start >= 0 && end > start, 'Exercise the actual polling transition handler');
const transition = source.slice(start, end);

function notification(previous, data) {
    const banners = [];
    const timers = [];
    const window = {_lastSessionStatus: previous};
    const context = vm.createContext({window, data,
        _showTimerBanner: (message, type) => banners.push({message, type}),
        _hideTimerBanner: () => {},
        setTimeout: (callback, delay) => timers.push(delay),
    });
    vm.runInContext(transition, context);
    assert.equal(window._lastSessionStatus, data.status);
    return {banners, timers};
}

// User, disconnect and unclassified administrator pauses have the same public
// status. None may claim an administrator caused it without explicit evidence.
for (const session of [undefined, {}, {power_paused: false}]) {
    const result = notification('active', {status: 'paused', session});
    assert.equal(result.banners.length, 1);
    assert.equal(result.banners[0].message,
        '⏸️ Your session is paused. Your remaining time is saved.');
    assert.equal(result.banners[0].type, 'warning');
}
const power = notification('active', {status: 'paused', session: {power_paused: true}});
assert.equal(power.banners[0].message,
    '⏸️ Your time was saved after a power interruption. Resume when ready.');
assert.equal(notification('paused', {status: 'paused'}).banners.length, 0,
    'Repeated polls must not repeatedly announce the same pause');
assert.equal(notification(undefined, {status: 'paused'}).banners.length, 0,
    'Initial load is not a newly observed pause');
assert.equal(notification('active', {status: 'paused', isp_outage: true}).banners.length, 0,
    'Outages keep their existing separate notification');
const resumed = notification('paused', {status: 'active'});
assert.equal(resumed.banners[0].message, '▶️ Your session has been resumed! Internet reconnected.');
assert.equal(resumed.banners[0].type, 'success');
assert.deepEqual(resumed.timers, [5000]);
assert(!source.includes('Your session has been paused by the administrator.'),
    'The misleading notification must not remain elsewhere in the portal');
console.log('Pause notifications: neutral pause message, power recovery, repeat polls, initial load, outages and resume passed.');
