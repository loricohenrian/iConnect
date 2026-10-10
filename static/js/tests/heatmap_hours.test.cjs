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
vm.runInContext("initHeatmap('heatmap-container', [{weekday: 1, hour: 5, count: 999, revenue: 999}, {weekday: 1, hour: 6, count: 2, revenue: 10}, {weekday: 1, hour: 12, count: 3, revenue: 15}, {weekday: 7, hour: 17, count: 4, revenue: 20}, {weekday: 7, hour: 18, count: 888, revenue: 888}])", context);
const headers = [...container.innerHTML.matchAll(/class="heatmap-hour-label"[^>]*>([^<]+)</g)].map(match => match[1]);
assert.equal(headers.length, 12);
for (let hour = 6; hour < 18; hour++) {
    assert.equal(headers[hour - 6], `${hour % 12 || 12} ${hour < 12 ? 'AM' : 'PM'}`);
}
assert(container.innerHTML.includes('6:00 AM – 7:00 AM'));
assert(container.innerHTML.includes('12:00 PM – 1:00 PM'));
assert(container.innerHTML.includes('5:00 PM – 6:00 PM'));
assert.equal([...container.innerHTML.matchAll(/class="heatmap-cell /g)].length, 84);
assert(container.innerHTML.includes('data-count="3" data-revenue="15"'));
assert(container.innerHTML.includes('heat-4" data-day="Sat" data-hour="17"'));
assert(!container.innerHTML.includes('data-hour="5"'));
assert(!container.innerHTML.includes('data-hour="18"'));
assert(!container.innerHTML.includes('data-count="999"'));
for (const hour of [...container.innerHTML.matchAll(/data-hour="(\d+)"/g)]) {
    assert(Number(hour[1]) >= 6 && Number(hour[1]) < 18);
}

vm.runInContext("initHeatmap('heatmap-container', [])", context);
assert.equal([...container.innerHTML.matchAll(/class="heatmap-cell heat-0/g)].length, 84);

const css = fs.readFileSync(path.join(__dirname, '..', '..', 'css', 'dashboard.css'), 'utf8');
const grid = css.match(/\.heatmap-grid\s*\{([^}]+)\}/)[1];
assert(grid.includes('grid-template-columns: 54px repeat(12, 1fr)'));

const template = fs.readFileSync(path.join(__dirname, '..', '..', '..', 'dashboard', 'templates', 'dashboard', 'heatmap.html'), 'utf8');
const summaryStart = template.indexOf('function updateHeatmapSummaryCards(');
const summaryEnd = template.indexOf('async function loadHeatmapLive(', summaryStart);
assert(summaryStart >= 0 && summaryEnd > summaryStart);
const cards = {'morning-peak': {innerHTML: ''}, 'afternoon-peak': {innerHTML: ''}, 'overall-peak': {innerHTML: ''}};
const summaryContext = vm.createContext({document: {getElementById: id => cards[id]}});
vm.runInContext(template.slice(summaryStart, summaryEnd), summaryContext);
vm.runInContext('updateHeatmapSummaryCards([{weekday: 1, hour: 5, count: 999, revenue: 999}, {weekday: 2, hour: 6, count: 2, revenue: 10}, {weekday: 3, hour: 17, count: 3, revenue: 15}, {weekday: 1, hour: 18, count: 888, revenue: 888}])', summaryContext);
assert(cards['morning-peak'].innerHTML.includes('Mon @ 6:00 AM'));
assert(cards['afternoon-peak'].innerHTML.includes('Tue @ 5:00 PM'));
assert(cards['overall-peak'].innerHTML.includes('Tue @ 5:00 PM'));
assert(cards['overall-peak'].innerHTML.includes('5 sessions'));
assert(cards['overall-peak'].innerHTML.includes('₱25'));
assert(cards['overall-peak'].innerHTML.includes('Top Day: <strong>Tue</strong>'));
vm.runInContext('updateHeatmapSummaryCards([{weekday: 1, hour: 5, count: 999, revenue: 999}, {weekday: 1, hour: 18, count: 888, revenue: 888}])', summaryContext);
assert(cards['overall-peak'].innerHTML.includes('Peak Hour: None yet'));
assert(cards['morning-peak'].innerHTML.includes('No morning traffic yet'));
assert(cards['afternoon-peak'].innerHTML.includes('No afternoon traffic yet'));
console.log('Heatmap: 6 AM–5 PM labels, 84 cells, matching grid, intensity and summary cards passed.');
