const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'portal.js'), 'utf8');
const start = source.indexOf('const NETWORK_FULL_MESSAGE');
const end = source.indexOf('function isCoinCooldownActive()', start);
const portal = {dataset: {networkFull: 'false', sessionActive: 'false'}};
const notice = {hidden: true};
function button() {
    return {dataset: {}, disabled: false, title: '', attributes: {},
            getAttribute(name) { return this.attributes[name]; }};
}
const buttons = {'request-slot-btn': button(), 'extend-request-btn': button(), 'btn-group-request-slot': button()};
const context = vm.createContext({document: {
    querySelector: () => portal,
    getElementById: id => id === 'network-full-notice' ? notice : buttons[id],
    addEventListener: () => {},
}});
vm.runInContext(source.slice(start, end), context);
context.updateCoinCapacity({available: 0}, null);
assert.equal(notice.hidden, false);
for (const btn of Object.values(buttons)) assert.equal(btn.disabled, true);
context.setCoinRequestDisabled(buttons['request-slot-btn'], false);
assert.equal(buttons['request-slot-btn'].disabled, true, 'Other flows cannot re-enable a full-network request');
context.updateCoinCapacity({available: 0}, 'active');
assert.equal(notice.hidden, true);
assert.equal(buttons['extend-request-btn'].disabled, false, 'Active users may extend at capacity');
context.updateCoinCapacity({available: 0}, 'paused');
assert.equal(buttons['extend-request-btn'].disabled, true, 'Paused users need a free slot');
context.updateCoinCapacity({available: 0}, 'paused', true);
assert.equal(buttons['extend-request-btn'].disabled, false, 'A newer paused voucher cannot hide an actual active session');
context.updateCoinCapacity({available: 0}, 'paused', false);
context.setCoinRequestDisabled(buttons['request-slot-btn'], true);
context.updateCoinCapacity({available: 1}, null);
assert.equal(buttons['extend-request-btn'].disabled, false);
assert.equal(buttons['request-slot-btn'].disabled, true, 'Reopening capacity preserves in-flight flow locks');
buttons['extend-request-btn'].attributes['data-outage-disabled'] = '1';
context.refreshCoinCapacityControls();
assert.equal(buttons['extend-request-btn'].disabled, true, 'Outage locks remain enforced');
assert.equal(notice.hidden, true);
assert(!/(requestBtn|extendRequestBtn|btnGroupRequestSlot|insertBtn)\.disabled =/.test(source));
const css = fs.readFileSync(path.join(__dirname, '..', '..', 'css', 'portal.css'), 'utf8');
assert(/#network-full-notice\[hidden\]\s*\{\s*display:\s*none;/.test(css), 'Hidden notices must override alert flex styling');
const template = fs.readFileSync(path.join(__dirname, '..', '..', '..', 'dashboard', 'templates', 'dashboard', 'sessions.html'), 'utf8');
const resumeStart = template.indexOf("if (currentAction === 'resume_all')");
const resumeEnd = template.indexOf('let body = null;', resumeStart);
assert(template.slice(resumeStart, resumeEnd).includes('alert(data.message'));
console.log('Portal capacity: new/paused users blocked, active extensions allowed, flow/outage locks preserved, admin result displayed.');
