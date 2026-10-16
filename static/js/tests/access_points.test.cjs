const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'access_points.js'), 'utf8');

async function run({failed = false, stale = false, hidden = false, absent = false, missing = false} = {}) {
    const cards = ['ap-1', 'ap-2'].map(id => {
        const fields = {status: {textContent: '', dataset: {}}};
        return {dataset: {apId: id}, fields, querySelector: selector => fields[selector.match(/"([^"]+)"/)[1]]};
    });
    let calls = 0;
    let interval;
    let visibility;
    const document = {
        hidden,
        getElementById: () => absent ? null : {querySelectorAll: () => cards},
        addEventListener: (_, callback) => { visibility = callback; },
    };
    const context = vm.createContext({document, AbortController, Date,
        setTimeout: () => 1, clearTimeout: () => {}, setInterval: callback => { interval = callback; },
        fetch: async () => {
            calls++;
            if (failed) throw new Error('offline');
            return {ok: true, json: async () => ({access_points: missing ? [] : cards.map(card => ({
                id: card.dataset.apId, status: card.dataset.apId === 'ap-1' ? 'reachable' : 'unreachable', ip: '<script>not HTML</script>',
                reason: 'Fresh reply', last_checked: new Date(Date.now() - (stale ? 100000 : 0)).toISOString(),
                last_seen: new Date().toISOString(),
            }))})};
        },
    });
    vm.runInContext(source, context);
    await new Promise(resolve => setImmediate(resolve));
    if (interval && !hidden) await interval();
    return {cards, calls, document, visibility};
}
(async () => {
    const valid = await run();
    assert.equal(valid.cards[0].fields.status.textContent, 'Reachable');
    assert.equal(valid.cards[0].fields.status.dataset.state, 'reachable');
    assert.equal(valid.cards[1].fields.status.textContent, 'Unreachable');
    assert.equal(valid.cards[1].fields.status.dataset.state, 'unreachable');
    for (const options of [{failed: true}, {stale: true}, {missing: true}]) {
        const result = await run(options);
        assert.equal(result.cards[0].fields.status.textContent, 'Unknown');
    }
    assert.equal((await run({absent: true})).calls, 0);
    const hidden = await run({hidden: true});
    assert.equal(hidden.calls, 0);
    hidden.document.hidden = false;
    await hidden.visibility();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(hidden.cards[0].fields.status.textContent, 'Reachable');
    console.log('AP monitoring browser-script cases passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
