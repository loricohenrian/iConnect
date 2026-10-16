/* Monitoring only: never changes AP settings or customer sessions. */
(() => {
    const panel = document.getElementById('access-point-monitor');
    if (!panel) return;
    const cards = Array.from(panel.querySelectorAll('[data-ap-id]'));
    let busy = false;
    const labels = {reachable: 'Reachable', unreachable: 'Unreachable', unknown: 'Unknown'};
    function status(card, state) {
        const badge = card.querySelector('[data-ap="status"]');
        badge.textContent = labels[state];
        badge.dataset.state = state;
    }
    function unknown() {
        cards.forEach(card => status(card, 'unknown'));
    }
    async function refresh() {
        if (busy || document.hidden) return;
        busy = true;
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 8000);
        try {
            const response = await fetch('/api/dashboard/access-points/', {
                credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
            });
            if (!response.ok) throw new Error('Request failed');
            const data = await response.json();
            if (!Array.isArray(data.access_points)) throw new Error('Invalid response');
            cards.forEach(card => {
                const row = data.access_points.find(item => item.id === card.dataset.apId);
                if (!row) { status(card, 'unknown'); return; }
                const stamp = Date.parse(row.last_checked);
                const age = Date.now() - stamp;
                const fresh = Number.isFinite(stamp) && age >= -5000 && age <= 90000;
                const state = fresh && labels[row.status] ? row.status : 'unknown';
                status(card, state);
            });
        } catch (_) {
            unknown();
        } finally {
            clearTimeout(timer);
            busy = false;
        }
    }
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
    refresh();
    setInterval(refresh, 30000);
})();
