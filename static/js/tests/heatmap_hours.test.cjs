const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'dashboard.js'), 'utf8');
const start = source.indexOf('function initHeatmap(');
const nextFunction = source.indexOf('// ROI Progress', start);
assert(start >= 0 && nextFunction > start);
const container = { innerHTML: '' };
const context = vm.createContext({ document: { getElementById: () => container } });
vm.runInContext(source.slice(start, nextFunction), context);
vm.runInContext("initHeatmap('heatmap-container', [{weekday: 1, hour: 12, count: 3, revenue: 15}])", context);
const headers = [...container.innerHTML.matchAll(/class="heatmap-hour-label"[^>]*>([^<]+)</g)].map(match => match[1]);
assert.equal(headers.length, 24);
for (let hour = 0; hour < 24; hour++) {
    assert.equal(headers[hour], `${hour % 12 || 12} ${hour < 12 ? 'AM' : 'PM'}`);
}
assert(container.innerHTML.includes('12:00 AM – 1:00 AM'));
assert(container.innerHTML.includes('12:00 PM – 1:00 PM'));
assert(container.innerHTML.includes('11:00 PM – 12:00 AM'));
assert.equal([...container.innerHTML.matchAll(/class="heatmap-cell /g)].length, 168);
assert(container.innerHTML.includes('data-count="3" data-revenue="15"'));
console.log('Heatmap: all 24 AM/PM labels, boundary tooltips and 168 cells passed.');
