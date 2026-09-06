/**
 * iConnect portal JavaScript.
 * Handles plan selection, countdown, and voucher submission.
 */

const MAC_ADDRESS_RE = /^([0-9A-F]{2}:){5}[0-9A-F]{2}$/;

class SessionTimer {
    constructor(elementId, totalSeconds) {
        this.element = document.getElementById(elementId);
        this.serverSeconds = Math.max(0, Number(totalSeconds) || 0);
        this.startedAt = this._now();
        this.interval = null;
        this.onExpire = null;
        this.onWarning = null;
        this.warningShown = false;
        this.oneMinWarningShown = false;
        this.expiredNotified = false;
        this.isPaused = false;
    }

    _now() {
        return (typeof performance !== "undefined" && performance.now) ? performance.now() : Date.now();
    }

    get remaining() {
        if (this.isPaused) {
            return Math.max(0, this.serverSeconds);
        }
        const elapsed = (this._now() - this.startedAt) / 1000;
        return Math.max(0, this.serverSeconds - elapsed);
    }

    setRemaining(seconds) {
        this.serverSeconds = Math.max(0, Number(seconds) || 0);
        this.startedAt = this._now();
        this.isPaused = false;
        this.warningShown = this.remaining <= 300;
        this.oneMinWarningShown = this.remaining <= 60;
        this.expiredNotified = false;
        this.update();
    }

    pause() {
        this.isPaused = true;
        this.serverSeconds = this.remaining;
        this.stop();
        this.update();
        if (typeof _hideTimerBanner === "function") _hideTimerBanner();
    }

    resume() {
        if (!this.isPaused) return;
        this.isPaused = false;
        this.startedAt = this._now();
        this.start();
    }

    start() {
        this.isPaused = false;
        this.update();
        this.interval = setInterval(() => {
            this.update();

            // 5-minute warning
            if (this.remaining <= 300 && this.remaining > 60 && !this.warningShown) {
                this.warningShown = true;
                _playAlertSound('warning');
                if (navigator.vibrate) try { navigator.vibrate([200, 100, 200]); } catch (e) {}
                _showTimerBanner('⚠️ 5 minutes remaining! Your session will end soon.', 'warning');
                _sendBrowserNotification('iConnect - 5 Minutes Left', 'Your WiFi session will end in 5 minutes. Consider extending your session.');
                if (this.onWarning) {
                    this.onWarning();
                }
            }

            // 1-minute warning
            if (this.remaining <= 60 && this.remaining > 0 && !this.oneMinWarningShown) {
                this.oneMinWarningShown = true;
                _playAlertSound('urgent');
                if (navigator.vibrate) try { navigator.vibrate([300, 100, 300]); } catch (e) {}
                _showTimerBanner('🔴 1 minute remaining! Extend now to stay connected.', 'danger');
                _sendBrowserNotification('iConnect - 1 Minute Left!', 'Your WiFi session ends in 1 minute! Insert coins to extend.');
            }

            // Expired
            if (this.remaining <= 0) {
                this.stop();
                if (!this.expiredNotified) {
                    this.expiredNotified = true;
                    _playAlertSound('expired');
                    _showTimerBanner('❌ Session expired. You have been disconnected.', 'expired');
                    _sendBrowserNotification('iConnect - Session Expired', 'Your WiFi session has ended. Insert coins to start a new session.');
                }
                if (this.onExpire) {
                    this.onExpire();
                }
            }
        }, 1000);

        // Recalculate immediately when user returns to tab
        document.addEventListener("visibilitychange", () => {
            if (!document.hidden && !this.isPaused) {
                this.update();
                if (this.remaining <= 0 && this.onExpire) {
                    this.stop();
                    this.onExpire();
                }
            }
        });
    }

    stop() {
        if (this.interval) {
            clearInterval(this.interval);
            this.interval = null;
        }
    }

    update() {
        if (!this.element) {
            return;
        }

        const safeRemaining = Math.max(0, Math.floor(this.remaining));
        const hours = Math.floor(safeRemaining / 3600);
        const minutes = Math.floor((safeRemaining % 3600) / 60);
        const seconds = safeRemaining % 60;

        this.element.textContent =
            `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;

        this.element.classList.remove("timer-green", "timer-amber", "timer-red");
        if (safeRemaining > 600) {
            this.element.classList.add("timer-green");
        } else if (safeRemaining > 300) {
            this.element.classList.add("timer-amber");
        } else {
            this.element.classList.add("timer-red");
            // Pulse effect when under 1 minute
            if (safeRemaining <= 60 && safeRemaining > 0) {
                this.element.style.animation = 'pulse-urgent 1s ease-in-out infinite';
            }
        }
    }
}

// === Timer Notification Helpers ===

function _playAlertSound(type) {
    try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const oscillator = ctx.createOscillator();
        const gainNode = ctx.createGain();
        oscillator.connect(gainNode);
        gainNode.connect(ctx.destination);
        gainNode.gain.value = 0.3;

        if (type === 'warning') {
            // Two gentle beeps
            oscillator.frequency.value = 660;
            oscillator.type = 'sine';
            oscillator.start();
            gainNode.gain.setValueAtTime(0.3, ctx.currentTime);
            gainNode.gain.setValueAtTime(0, ctx.currentTime + 0.15);
            gainNode.gain.setValueAtTime(0.3, ctx.currentTime + 0.3);
            gainNode.gain.setValueAtTime(0, ctx.currentTime + 0.45);
            oscillator.stop(ctx.currentTime + 0.5);
        } else if (type === 'urgent') {
            // Three rapid beeps
            oscillator.frequency.value = 880;
            oscillator.type = 'sine';
            oscillator.start();
            gainNode.gain.setValueAtTime(0.4, ctx.currentTime);
            gainNode.gain.setValueAtTime(0, ctx.currentTime + 0.12);
            gainNode.gain.setValueAtTime(0.4, ctx.currentTime + 0.2);
            gainNode.gain.setValueAtTime(0, ctx.currentTime + 0.32);
            gainNode.gain.setValueAtTime(0.4, ctx.currentTime + 0.4);
            gainNode.gain.setValueAtTime(0, ctx.currentTime + 0.52);
            oscillator.stop(ctx.currentTime + 0.6);
        } else if (type === 'expired') {
            // Descending tone
            oscillator.frequency.setValueAtTime(880, ctx.currentTime);
            oscillator.frequency.linearRampToValueAtTime(220, ctx.currentTime + 0.8);
            oscillator.type = 'sine';
            gainNode.gain.setValueAtTime(0.4, ctx.currentTime);
            gainNode.gain.linearRampToValueAtTime(0, ctx.currentTime + 1);
            oscillator.start();
            oscillator.stop(ctx.currentTime + 1);
        }
    } catch (e) {
        // Web Audio not supported — fail silently
    }
}

function _showTimerBanner(message, type) {
    // Remove existing banner if any
    const existing = document.getElementById('timer-alert-banner');
    if (existing) existing.remove();

    const banner = document.createElement('div');
    banner.id = 'timer-alert-banner';
    banner.innerHTML = `<span>${message}</span><button onclick="this.parentElement.remove()" style="background:none;border:none;color:inherit;font-size:18px;cursor:pointer;padding:0 0 0 12px;">✕</button>`;

    let bgColor, textColor;
    if (type === 'warning') {
        bgColor = '#FFFBEB';
        textColor = '#92400E';
    } else if (type === 'danger') {
        bgColor = '#FEF2F2';
        textColor = '#991B1B';
    } else {
        bgColor = '#7B2D3B';
        textColor = '#FFFFFF';
    }

    banner.style.cssText = `
        position: fixed; top: 0; left: 0; right: 0; z-index: 9999;
        background: ${bgColor}; color: ${textColor};
        padding: 10px 16px; font-size: 13px; font-weight: 600;
        text-align: center; display: flex; align-items: center;
        justify-content: center; box-shadow: 0 2px 8px rgba(45, 31, 31, 0.1);
        border-bottom: 1px solid rgba(0, 0, 0, 0.08);
        animation: slideDown 0.4s ease-out;
    `;

    // Add animation keyframes if not already present
    if (!document.getElementById('timer-alert-styles')) {
        const style = document.createElement('style');
        style.id = 'timer-alert-styles';
        style.textContent = `
            @keyframes slideDown {
                from { transform: translateY(-100%); opacity: 0; }
                to { transform: translateY(0); opacity: 1; }
            }
            @keyframes pulse-urgent {
                0%, 100% { transform: scale(1); }
                50% { transform: scale(1.03); }
            }
        `;
        document.head.appendChild(style);
    }

    document.body.prepend(banner);
}

function _hideTimerBanner() {
    const existing = document.getElementById('timer-alert-banner');
    if (existing) existing.remove();
}

async function _sendBrowserNotification(title, body, extraOptions = {}) {
    if (!('Notification' in window)) return;
    if (Notification.permission !== 'granted') return;

    const defaultOptions = {
        body: body,
        icon: '/static/icons/icon-192.png',
        badge: '/static/icons/icon-192.png',
        vibrate: [200, 100, 200],
        tag: 'iconnect-expiry-alert',
        renotify: true,
        requireInteraction: true,
        data: { url: '/session/' }
    };
    const options = Object.assign(defaultOptions, extraOptions);

    // 1. Mobile Android Chrome / Samsung Internet: Requires ServiceWorker registration
    try {
        if ('serviceWorker' in navigator) {
            const reg = await navigator.serviceWorker.getRegistration();
            if (reg && reg.showNotification) {
                return reg.showNotification(title, options);
            }
        }
    } catch (e) {
        console.warn('Service worker notification failed:', e);
    }

    // 2. Desktop fallback
    try {
        new Notification(title, options);
    } catch (e) {
        console.warn('Desktop notification fallback failed:', e);
    }
}

function _showExpiredModal(macAddress) {
    if (document.getElementById('expired-modal-overlay')) return;

    const modal = document.createElement('div');
    modal.id = 'expired-modal-overlay';
    modal.className = 'modal-overlay';
    modal.style.display = 'flex';

    modal.innerHTML = `
        <div class="modal-card animate-fadeIn text-center" style="max-width: 360px;">
            <div style="
                width: 56px; height: 56px; background: #FEF2F2;
                color: #DC2626; border-radius: 50%; display: flex;
                align-items: center; justify-content: center; font-size: 26px;
                margin: 0 auto 14px; border: 1px solid #FEE2E2;
            ">
                <i class="bi bi-wifi-off"></i>
            </div>
            <h3 style="margin: 0 0 8px; font-size: 1.15rem; font-weight: 700; color: var(--color-dark);">Session Expired</h3>
            <p style="margin: 0 0 20px; color: var(--color-gray); font-size: 13px; line-height: 1.45;">
                Your WiFi time has run out. Insert coins to start a new session and stay connected.
            </p>
            <button onclick="window.location.href = buildPortalUrl('/', '${macAddress}', { expired: 1 })" class="btn btn-primary w-100">
                Start New Session
            </button>
        </div>
    `;
    document.body.appendChild(modal);
}

function initPlanSelection() {
    const planCards = document.querySelectorAll(".plan-card");
    const selectedPlanInput = document.getElementById("selected-plan");

    // Group Pass UI Elements
    const toggleGroup = document.getElementById("group-pass-toggle");
    const configGroup = document.getElementById("group-pass-config");
    const fpPlus = document.getElementById("fp-plus");
    const fpMinus = document.getElementById("fp-minus");
    const fpCount = document.getElementById("fp-device-count");
    const fpPrice = document.getElementById("family-pass-price");
    let fpDevices = 2; // Default to 2 devices for group pass
    let currentPlanPrice = 0;

    function updateGroupPassPrice() {
        if (fpPrice && toggleGroup && toggleGroup.checked) {
            fpPrice.innerText = `₱${currentPlanPrice * fpDevices}`;
        }
    }

    if (toggleGroup && configGroup) {
        toggleGroup.addEventListener("change", () => {
            if (toggleGroup.checked) {
                configGroup.style.display = "flex";
                const selectedPlanInput = document.getElementById("selected-plan");
                if (!currentPlanPrice && selectedPlanInput && selectedPlanInput.value) {
                    const card = document.querySelector(`.plan-card[data-plan-id="${selectedPlanInput.value}"]`);
                    if (card) {
                        const priceText = card.querySelector('.plan-price').innerText.replace('₱', '');
                        currentPlanPrice = parseInt(priceText) || 0;
                    }
                }
            } else {
                configGroup.style.display = "none";
            }
            updateGroupPassPrice();
        });
    }

    if (fpPlus && fpMinus && fpCount) {
        fpPlus.addEventListener("click", (e) => {
            e.stopPropagation();
            if (fpDevices < 10) { fpDevices++; fpCount.innerText = fpDevices; updateGroupPassPrice(); }
        });
        fpMinus.addEventListener("click", (e) => {
            e.stopPropagation();
            if (fpDevices > 2) { fpDevices--; fpCount.innerText = fpDevices; updateGroupPassPrice(); }
        });
    }

    planCards.forEach((card) => {
        if (card.closest("#ratesModal")) {
            return;
        }

        card.addEventListener("click", () => {
            planCards.forEach((item) => item.classList.remove("selected"));
            card.classList.add("selected");

            if (selectedPlanInput) {
                selectedPlanInput.value = card.dataset.planId;
                const priceText = card.querySelector('.plan-price').innerText.replace('₱', '');
                currentPlanPrice = parseInt(priceText) || 0;
                updateGroupPassPrice();
            }

            const requestBtn = document.getElementById("request-slot-btn");
            if (requestBtn) {
                requestBtn.disabled = false;
                setTimeout(() => {
                    requestBtn.scrollIntoView({ behavior: "smooth", block: "center" });
                }, 80);
            }

            if (typeof window.onPortalPlanSelected === "function") {
                window.onPortalPlanSelected(selectedPlanInput.value);
            }
        });
    });
}

function escapeHtml(text) {
    return String(text)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/\"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function renderAnnouncements(announcements) {
    const container = document.getElementById("portal-announcements");
    if (!container) {
        return;
    }

    if (!Array.isArray(announcements) || announcements.length === 0) {
        container.innerHTML = "";
        container.style.display = "none";
        return;
    }

    container.style.display = "";
    container.innerHTML = announcements
        .map((announcement) => {
            const safeMessage = escapeHtml(announcement.message || "");
            return `
                <div class="announcement-banner" data-announcement-id="${announcement.id}">
                    <i class="bi bi-megaphone-fill"></i>
                    <span>${safeMessage}</span>
                </div>
            `;
        })
        .join("");
}

function renderPlans(plans) {
    const planGrid = document.getElementById("plan-grid");
    if (!planGrid) {
        return;
    }

    const selectedPlanInput = document.getElementById("selected-plan");
    const requestBtn = document.getElementById("request-slot-btn");
    const startBtn = document.getElementById("start-session-btn");
    const insertCoinsSection = document.getElementById("insert-coins-section");
    const selectedPlanId = selectedPlanInput ? selectedPlanInput.value : "";

    if (!Array.isArray(plans) || plans.length === 0) {
        planGrid.innerHTML = `
            <div class="empty-state" id="plans-empty-state">
                <i class="bi bi-wifi-off"></i>
                <p>No plans available</p>
                <small>Please contact the administrator</small>
            </div>
        `;
        if (insertCoinsSection) {
            insertCoinsSection.style.display = "none";
        }
        if (selectedPlanInput) {
            selectedPlanInput.value = "";
        }
        if (requestBtn) {
            requestBtn.disabled = true;
        }
        if (startBtn) {
            startBtn.disabled = true;
            startBtn.dataset.readyToStart = "0";
        }
        return;
    }

    if (insertCoinsSection) {
        insertCoinsSection.style.display = "";
    }

    if (planGrid) {
        planGrid.innerHTML = plans
            .map((plan) => {
                const popularBadge =
                    plan.is_most_popular
                        ? '<div class="plan-popular">Popular</div>'
                        : "";

                const speedHtml = plan.speed_limit
                    ? `<div class="plan-speed">Up to ↓${plan.speed_limit}${plan.speed_limit_upload ? ' / ↑' + plan.speed_limit_upload : ''} Mbps</div>`
                    : '';

                return `
                    <div class="plan-card" data-plan-id="${plan.id}" id="plan-${plan.id}">
                        ${popularBadge}
                        <div class="plan-price">₱${plan.price}</div>
                        <div class="plan-duration">${escapeHtml(plan.duration_display)}</div>
                        ${speedHtml}
                    </div>
                `;
            })
            .join("");

        initPlanSelection();

        const selectedCard = selectedPlanId
            ? document.querySelector(`.plan-card[data-plan-id="${selectedPlanId}"]`)
            : null;

        if (selectedCard) {
            selectedCard.classList.add("selected");
        }

        // As long as plans exist, Insert Coins is available for solo users
        if (requestBtn) {
            requestBtn.disabled = false;
        }
        if (startBtn && startBtn.dataset.readyToStart !== "1") {
            startBtn.disabled = true;
        }
    }

    // Also sync extend plan grid if on extend session page
    const extendPlanGrid = document.getElementById("extend-plan-grid");
    if (extendPlanGrid) {
        extendPlanGrid.innerHTML = plans
            .map((plan) => {
                const popularBadge =
                    plan.is_most_popular
                        ? '<div class="plan-popular">Popular</div>'
                        : "";

                const speedHtml = plan.speed_limit
                    ? `<div class="plan-speed">Up to ↓${plan.speed_limit}${plan.speed_limit_upload ? ' / ↑' + plan.speed_limit_upload : ''} Mbps</div>`
                    : '';

                return `
                    <div class="plan-card" data-plan-id="${plan.id}" id="plan-${plan.id}" style="cursor: default;">
                        ${popularBadge}
                        <div class="plan-price">₱${plan.price}</div>
                        <div class="plan-duration">+${escapeHtml(plan.duration_display)}</div>
                        ${speedHtml}
                    </div>
                `;
            })
            .join("");
    }

    const extendRequestBtn = document.getElementById("extend-request-btn");
    if (extendRequestBtn) {
        extendRequestBtn.disabled = !Array.isArray(plans) || plans.length === 0;
    }
}

function renderSmartCombos(combos) {
    const list = document.getElementById("smart-combo-list");
    if (!list || !Array.isArray(combos)) return;
    const card = list.closest(".smart-combo-card");
    if (combos.length === 0) {
        if (card) card.style.display = "none";
        return;
    }
    if (card) card.style.display = "";

    list.innerHTML = combos.map(combo => `
        <div class="smart-combo-item">
            <div class="smart-combo-coin">₱${escapeHtml(combo.amount)}</div>
            <div class="smart-combo-formula">${escapeHtml(combo.breakdown)}</div>
            <div class="smart-combo-duration">${escapeHtml(combo.duration)}</div>
        </div>
    `).join("");
}

let _lastPlansJson = "";

async function syncPortalLiveData() {
    try {
        const response = await fetch(`/api/portal/live-data/?t=${Date.now()}`, {
            headers: { "Cache-Control": "no-cache" },
        });

        if (!response.ok) return;

        const data = await response.json();
        renderAnnouncements(data.announcements || []);

        // Only re-render plans if they actually changed (prevents blinking)
        const plansJson = JSON.stringify(data.plans || []);
        if (plansJson !== _lastPlansJson) {
            _lastPlansJson = plansJson;
            renderPlans(data.plans || []);
            const isExtendPage = !!document.getElementById("extend-plan-grid");
            const combos = isExtendPage ? data.smart_combo_examples_extend : data.smart_combo_examples;
            renderSmartCombos(combos);
        }

        // Update connection slots in real-time
        if (data.slots) {
            _updateSlotsIndicator(data.slots);
        }
    } catch (error) {
        console.error("Live data sync error:", error);
    }
}

function _updateSlotsIndicator(slots) {
    const badge = document.getElementById('slots-badge');
    const dot = document.getElementById('slots-dot');
    const text = document.getElementById('slots-text');
    if (!badge || !dot || !text) return;

    const { active, max: maxSlots, available } = slots;

    // Update text
    // Update text
    if (available > 0) {
        text.textContent = `${available} / ${maxSlots} slots available`;
    } else {
        text.textContent = 'Full — please try again shortly';
    }

    // Update classes based on availability
    badge.className = 'slots-badge ' + (available > 5 ? 'available' : (available > 0 ? 'low' : 'full'));
    badge.removeAttribute('style');
    dot.removeAttribute('style');
}

function initPortalRealtime() {
    let liveDataIntervalId = null;

    const resetInterval = () => {
        if (liveDataIntervalId) {
            clearInterval(liveDataIntervalId);
        }

        const intervalMs = document.hidden ? 30000 : 3000;
        liveDataIntervalId = setInterval(syncPortalLiveData, intervalMs);
    };

    syncPortalLiveData();
    resetInterval();
    document.addEventListener("visibilitychange", () => {
        resetInterval();
        if (!document.hidden) {
            syncPortalLiveData();
        }
    });
}

async function parseJsonSafe(response) {
    try {
        return await response.json();
    } catch (error) {
        return {};
    }
}

function setStartFlowMessage(message, type = "info") {
    const messageEl = document.getElementById("start-flow-message");
    if (!messageEl) {
        return;
    }

    if (!message) {
        messageEl.style.display = "none";
        messageEl.textContent = "";
        return;
    }

    const classMap = {
        success: "alert-success",
        warning: "alert-warning",
        danger: "alert-danger",
        error: "alert-danger",
        info: "alert-info",
    };

    messageEl.className = `alert ${classMap[type] || "alert-info"} mt-md`;
    messageEl.textContent = message;
    messageEl.style.display = "block";
}

function setStartFlowMeta(metaHtml) {
    const metaEl = document.getElementById("start-flow-meta");
    if (!metaEl) {
        return;
    }
    metaEl.innerHTML = metaHtml || "";
}

function formatCoinRequestMeta(coinRequest) {
    if (!coinRequest) {
        return "";
    }

    const status = (coinRequest.status || "").toUpperCase();
    const credited = Number(coinRequest.credited_amount || 0);
    const expected = Number(coinRequest.expected_amount || 0);

    let statusBadge = "";
    if (status === "ACTIVE") {
        statusBadge = `<span style="background:#10B981; color:#fff; padding:2px 8px; border-radius:6px; font-weight:700; font-size:11px; letter-spacing:0.5px; display:inline-block;">ACTIVE</span>`;
    } else if (status === "PENDING") {
        statusBadge = `<span style="background:#F59E0B; color:#fff; padding:2px 8px; border-radius:6px; font-weight:700; font-size:11px; letter-spacing:0.5px; display:inline-block;">PENDING</span>`;
    } else if (status === "COMPLETED") {
        statusBadge = `<span style="background:#3B82F6; color:#fff; padding:2px 8px; border-radius:6px; font-weight:700; font-size:11px; letter-spacing:0.5px; display:inline-block;">READY</span>`;
    } else {
        statusBadge = `<span style="background:#64748B; color:#fff; padding:2px 8px; border-radius:6px; font-weight:700; font-size:11px; letter-spacing:0.5px; display:inline-block;">${status}</span>`;
    }

    const parts = [];
    if (status) {
        parts.push(`Status: ${statusBadge}`);
    }
    if (coinRequest.is_group_pass) {
        parts.push(`Payment: <strong>₱${credited} / ₱${expected}</strong>`);
    } else if (coinRequest.combo_duration_display) {
        parts.push(`Coins Inserted: <strong>₱${credited}</strong> &nbsp;➔&nbsp; <span style="color:#10B981; font-weight:700;">⏱️ ${escapeHtml(coinRequest.combo_duration_display)}</span>`);
        if (coinRequest.combo_breakdown_text) {
            parts.push(`<span class="text-muted" style="font-size:11px;">(${escapeHtml(coinRequest.combo_breakdown_text)})</span>`);
        }
    } else {
        parts.push(`Coins Inserted: <strong>₱${credited}</strong>`);
    }

    return parts.join(" &nbsp;|&nbsp; ");
}

function coinRequestStatusMessage(coinRequest, context = "start") {
    if (!coinRequest) {
        return "Unable to read coin request status.";
    }

    const status = coinRequest.status;
    if (status === "completed") {
        return context === "extend"
            ? "Payment complete. Tap Extend Now to add more time."
            : "Payment complete. Tap Connect Now to start your session.";
    }
    if (status === "active") {
        if (coinRequest.ready_to_start) {
            const actionWord = context === "extend" ? "Extend Now" : "Connect Now";
            if (coinRequest.combo_duration_display) {
                return `Coins detected! You have ${coinRequest.combo_duration_display} ready. Insert more coins or tap ${actionWord}.`;
            }
            return `Coins detected! Insert more coins for more time, or tap ${actionWord}.`;
        }
        return "Insert coins now. Your device currently owns the coin slot window.";
    }
    if (status === "pending") {
        return "Request queued. Wait for your turn to insert coins.";
    }
    if (status === "expired") {
        return "Coin window expired. Tap Insert Coins again.";
    }
    if (status === "cancelled") {
        return "Coin request was cancelled. Tap Insert Coins to continue.";
    }
    return "Coin request updated.";
}

let activeCoinCountdownInterval = null;
let activeCoinCountdownEndTime = 0;
let lastCreditedCoinAmount = null;

function triggerCoinTimerPulse(display) {
    if (display) {
        display.style.transition = "color 0.2s ease, transform 0.2s ease";
        display.style.color = "#10B981";
        display.style.transform = "scale(1.1)";
        setTimeout(() => {
            display.style.color = "var(--text-primary)";
            display.style.transform = "scale(1)";
        }, 600);
    }
}

function updateCountdownDisplay(display) {
    if (!display) return;
    const remaining = Math.max(0, Math.floor((activeCoinCountdownEndTime - Date.now()) / 1000));
    const m = Math.floor(remaining / 60).toString().padStart(2, "0");
    const s = (remaining % 60).toString().padStart(2, "0");
    display.innerText = `${m}:${s}`;
    if (remaining <= 10) {
        display.style.color = "#EF4444";
    } else {
        display.style.color = "var(--text-primary)";
    }
}

function syncCoinCountdown(coinRequest) {
    const container = document.getElementById("coin-countdown-container");
    const display = document.getElementById("coin-countdown-display");

    if (!container || !display) return;

    if (!coinRequest || !["active", "pending"].includes(coinRequest.status)) {
        if (activeCoinCountdownInterval) {
            clearInterval(activeCoinCountdownInterval);
            activeCoinCountdownInterval = null;
        }
        container.style.display = "none";
        lastCreditedCoinAmount = null;
        return;
    }

    if (coinRequest.status === "active") {
        container.style.display = "block";
        const now = Date.now();
        let targetEndTime = 0;

        if (typeof coinRequest.remaining_seconds === "number") {
            targetEndTime = now + (coinRequest.remaining_seconds * 1000);
        } else if (coinRequest.expires_at) {
            targetEndTime = new Date(coinRequest.expires_at).getTime();
        }

        const currentCredited = Number(coinRequest.credited_amount || 0);
        const prevCredited = lastCreditedCoinAmount;
        lastCreditedCoinAmount = currentCredited;

        const isCoinAdded = prevCredited !== null && currentCredited > prevCredited;
        const isTimeExtended = targetEndTime > (activeCoinCountdownEndTime + 2000);

        if (isCoinAdded || isTimeExtended) {
            triggerCoinTimerPulse(display);
        }

        if (targetEndTime > 0) {
            activeCoinCountdownEndTime = targetEndTime;
        }

        updateCountdownDisplay(display);

        if (!activeCoinCountdownInterval) {
            activeCoinCountdownInterval = setInterval(() => {
                const remaining = Math.max(0, Math.floor((activeCoinCountdownEndTime - Date.now()) / 1000));
                const m = Math.floor(remaining / 60).toString().padStart(2, "0");
                const s = (remaining % 60).toString().padStart(2, "0");
                display.innerText = `${m}:${s}`;

                if (remaining <= 10) {
                    display.style.color = "#EF4444";
                } else {
                    display.style.color = "var(--text-primary)";
                }

                if (remaining <= 0) {
                    clearInterval(activeCoinCountdownInterval);
                    activeCoinCountdownInterval = null;
                }
            }, 1000);
        }
    } else {
        // Pending
        container.style.display = "none";
    }
}

window.syncCoinCountdown = syncCoinCountdown;

function initProductionStartFlow(macAddress) {
    const selectedPlanInput = document.getElementById("selected-plan");
    const requestBtn = document.getElementById("request-slot-btn");
    const startBtn = document.getElementById("start-session-btn");

    if (!selectedPlanInput || !requestBtn || !startBtn) {
        return;
    }

    const state = {
        requestId: null,
        planId: null,
        readyToStart: false,
        pollTimer: null,
        pollInFlight: false,
        isGroupPass: false,
        groupDevices: null,
    };

    startBtn.dataset.readyToStart = "0";
    startBtn.style.display = "none";

    const clearPolling = () => {
        if (state.pollTimer) {
            clearInterval(state.pollTimer);
            state.pollTimer = null;
        }
    };

    const applyCoinRequestState = (coinRequest) => {
        state.requestId = coinRequest ? coinRequest.id : null;
        state.readyToStart = Boolean(coinRequest && coinRequest.ready_to_start);
        if (coinRequest) {
            state.isGroupPass = coinRequest.is_group_pass || false;
            state.groupDevices = coinRequest.group_pass_devices || null;
            if (coinRequest.plan_id) {
                state.planId = coinRequest.plan_id;
            }
        }

        syncCoinCountdown(coinRequest);

        setStartFlowMessage(
            coinRequestStatusMessage(coinRequest),
            state.readyToStart
                ? "success"
                : ["expired", "cancelled"].includes(coinRequest?.status)
                    ? "warning"
                    : "info"
        );
        setStartFlowMeta(formatCoinRequestMeta(coinRequest));

        startBtn.disabled = !state.readyToStart;
        startBtn.dataset.readyToStart = state.readyToStart ? "1" : "0";

        const actionsContainer = document.getElementById("coin-actions-container");
        const hasCoins = Boolean(coinRequest && ((coinRequest.credited_amount && coinRequest.credited_amount > 0) || coinRequest.ready_to_start));
        const btnCancel = document.getElementById("btn-cancel-coin-request");
        const linkCancel = document.getElementById("link-cancel-coin-request");
        const isTerminal = ["expired", "cancelled"].includes(coinRequest?.status);

        if (coinRequest && (!isTerminal || state.readyToStart)) {
            if (actionsContainer) actionsContainer.style.display = "block";
        } else {
            if (actionsContainer) actionsContainer.style.display = "none";
        }

        if (hasCoins) {
            startBtn.style.display = "block";
            if (btnCancel) btnCancel.style.display = "none";
            if (linkCancel) linkCancel.style.display = "inline-block";
        } else {
            startBtn.style.display = "none";
            if (btnCancel) btnCancel.style.display = "inline-block";
            if (linkCancel) linkCancel.style.display = "none";
        }

        if (coinRequest?.status === "expired") {
            if (state.readyToStart) {
                setStartFlowMessage("Time expired. Auto-connecting with inserted coins...", "success");
                clearPolling();
                startBtn.click();
            } else {
                clearPolling();
            }
        } else if (coinRequest?.status === "cancelled") {
            clearPolling();
        }
    };

    const pollRequestStatus = async () => {
        if (!state.requestId || state.pollInFlight) {
            return;
        }

        state.pollInFlight = true;
        try {
            const response = await fetch(
                `/api/session/start/request-status/?request_id=${encodeURIComponent(state.requestId)}&mac_address=${encodeURIComponent(macAddress)}`
            );
            const data = await parseJsonSafe(response);

            if (!response.ok) {
                if (response.status === 404) {
                    clearPolling();
                    setStartFlowMessage("Coin request no longer exists. Please request again.", "warning");
                    setStartFlowMeta("");
                    startBtn.disabled = true;
                    startBtn.dataset.readyToStart = "0";
                    startBtn.style.display = "none";
                    const actionsContainer = document.getElementById("coin-actions-container");
                    if (actionsContainer) actionsContainer.style.display = "none";
                    const btnCancel = document.getElementById("btn-cancel-coin-request");
                    const linkCancel = document.getElementById("link-cancel-coin-request");
                    if (btnCancel) btnCancel.style.display = "inline-block";
                    if (linkCancel) linkCancel.style.display = "none";
                    return;
                }
                setStartFlowMessage(data.error || "Unable to check coin request status.", "warning");
                return;
            }

            applyCoinRequestState(data.coin_request);
        } catch (error) {
            setStartFlowMessage("Connection issue while checking queue status. Retrying...", "warning");
        } finally {
            state.pollInFlight = false;
        }
    };

    const startPolling = () => {
        clearPolling();
        state.pollTimer = setInterval(pollRequestStatus, 3000);
        pollRequestStatus();
    };
    
    // EXPORT TO GLOBAL SCOPE FOR GROUP PLAN FLOW
    window.applyCoinRequestState = applyCoinRequestState;
    window.startPolling = startPolling;

    const selectedPlanId = () => {
        const value = Number.parseInt(selectedPlanInput.value, 10);
        return Number.isInteger(value) && value > 0 ? value : null;
    };

    requestBtn.addEventListener("click", async () => {
        if (!macAddress) {
            setStartFlowMessage("Device identity missing. Re-open portal from WiFi login.", "danger");
            return;
        }

        requestBtn.disabled = true;
        startBtn.disabled = true;
        startBtn.dataset.readyToStart = "0";

        const planId = selectedPlanId() || state.planId || null;
        state.planId = planId;

        try {
            const bodyPayload = {
                mac_address: macAddress,
                is_group_pass: false,
            };
            if (planId) {
                bodyPayload.plan_id = planId;
            }

            const response = await fetch("/api/session/start/request/", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCSRFToken(),
                },
                body: JSON.stringify(bodyPayload),
            });
            const data = await parseJsonSafe(response);

            if (!response.ok) {
                setStartFlowMessage(data.error || "Unable to create coin request.", "danger");
                setStartFlowMeta("");
                return;
            }

            if (data.coin_request) {
                applyCoinRequestState(data.coin_request);
                if (!data.coin_request.ready_to_start) {
                    startPolling();
                }
            } else {
                setStartFlowMessage(data.message || "Coin request created.", "info");
            }
        } catch (error) {
            setStartFlowMessage("Connection error while requesting coin slot.", "danger");
        } finally {
            requestBtn.disabled = false;
        }
    });

    startBtn.addEventListener("click", async () => {
        const planId = state.planId || selectedPlanId() || null;
        if (state.isGroupPass && !planId) {
            setStartFlowMessage("Select a plan first.", "warning");
            startBtn.disabled = true;
            startBtn.dataset.readyToStart = "0";
            return;
        }

        if (!state.readyToStart) {
            setStartFlowMessage("Insert coins first, then tap Connect Now.", "warning");
            return;
        }

        requestBtn.disabled = true;
        startBtn.disabled = true;

        try {
            const payload = {
                mac_address: macAddress,
            };
            if (planId) {
                payload.plan_id = planId;
            }
            if (state.isGroupPass) {
                payload.is_group_pass = true;
                payload.group_pass_devices = state.groupDevices;
            }

            const response = await fetch("/api/session/start/", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCSRFToken(),
                },
                body: JSON.stringify(payload),
            });
            const data = await parseJsonSafe(response);

            if (response.ok) {
                window.location.href = buildPortalUrl("/session/", macAddress);
                return;
            }

            if (response.status === 402 && data.coin_request) {
                applyCoinRequestState(data.coin_request);
                if (!data.coin_request.ready_to_start) {
                    startPolling();
                }
            }

            setStartFlowMessage(data.error || "Failed to start session.", "danger");
        } catch (error) {
            setStartFlowMessage("Connection error while starting session.", "danger");
        } finally {
            requestBtn.disabled = false;
            if (!state.readyToStart) {
                startBtn.disabled = true;
                startBtn.dataset.readyToStart = "0";
            }
        }
    });

    window.onPortalPlanSelected = (planIdValue) => {
        const nextPlanId = Number.parseInt(planIdValue, 10);
        if (!Number.isInteger(nextPlanId) || nextPlanId <= 0) {
            return;
        }

        requestBtn.disabled = false;

        if (state.planId && state.planId !== nextPlanId) {
            clearPolling();
            state.requestId = null;
            state.readyToStart = false;
            startBtn.disabled = true;
            startBtn.dataset.readyToStart = "0";
            startBtn.style.display = "none";
            const actionsContainer = document.getElementById("coin-actions-container");
            if (actionsContainer) actionsContainer.style.display = "none";
            const btnCancel = document.getElementById("btn-cancel-coin-request");
            const linkCancel = document.getElementById("link-cancel-coin-request");
            if (btnCancel) btnCancel.style.display = "inline-block";
            if (linkCancel) linkCancel.style.display = "none";
            setStartFlowMessage("Plan changed. Request a new coin slot for this plan.", "info");
            setStartFlowMeta("");
        }

        state.planId = nextPlanId;
    };
}

function initVoucherInput() {
    // Legacy — kept for backwards compatibility, no-op now
}

function initExtendSessionFlow(macAddress) {
    const extendPlanInput = document.getElementById("extend-plan");
    const extendRequestBtn = document.getElementById("extend-request-btn");
    const extendNowBtn = document.getElementById("extend-now-btn");
    const extendPlanGrid = document.getElementById("extend-plan-grid");

    if (!extendPlanInput || !extendRequestBtn || !extendNowBtn) {
        return;
    }

    // Direct Insert Coins is enabled by default
    extendRequestBtn.disabled = false;
    extendNowBtn.disabled = true;
    extendNowBtn.style.display = "none";

    const state = {
        requestId: null,
        planId: null,
        readyToStart: false,
        pollTimer: null,
        pollInFlight: false,
    };

    const setExtendMessage = (message, type = "info") => {
        const el = document.getElementById("extend-flow-message");
        if (!el) return;
        if (!message) { el.style.display = "none"; return; }
        const classMap = { success: "alert-success", warning: "alert-warning", danger: "alert-danger", error: "alert-danger", info: "alert-info" };
        el.className = `alert ${classMap[type] || "alert-info"} mt-md`;
        el.textContent = message;
        el.style.display = "block";
    };

    const setExtendMeta = (text) => {
        const el = document.getElementById("extend-flow-meta");
        if (el) el.innerHTML = text || "";
    };

    const clearPolling = () => {
        if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
    };

    const applyCoinRequestState = (coinRequest) => {
        state.requestId = coinRequest ? coinRequest.id : null;
        state.readyToStart = Boolean(coinRequest && coinRequest.ready_to_start);
        if (coinRequest) {
            state.isGroupPass = Boolean(coinRequest.is_group_pass);
            state.groupDevices = coinRequest.group_pass_devices || null;
            if (coinRequest.plan_id) {
                state.planId = coinRequest.plan_id;
                if (extendPlanInput) extendPlanInput.value = coinRequest.plan_id;
            }
        }

        syncCoinCountdown(coinRequest);

        setExtendMessage(
            coinRequestStatusMessage(coinRequest, "extend"),
            state.readyToStart ? "success" :
                ["expired", "cancelled"].includes(coinRequest?.status) ? "warning" : "info"
        );
        setExtendMeta(formatCoinRequestMeta(coinRequest));

        extendNowBtn.disabled = !state.readyToStart;

        const actionsContainer = document.getElementById("coin-actions-container");
        const hasCoins = Boolean(coinRequest && ((coinRequest.credited_amount && coinRequest.credited_amount > 0) || coinRequest.ready_to_start));
        const btnCancel = document.getElementById("btn-cancel-coin-request");
        const linkCancel = document.getElementById("link-cancel-coin-request");
        const isTerminal = ["expired", "cancelled"].includes(coinRequest?.status);

        if (coinRequest && (!isTerminal || state.readyToStart)) {
            if (actionsContainer) actionsContainer.style.display = "block";
        } else {
            if (actionsContainer) actionsContainer.style.display = "none";
        }

        if (hasCoins) {
            extendNowBtn.style.display = "block";
            if (btnCancel) btnCancel.style.display = "none";
            if (linkCancel) linkCancel.style.display = "inline-block";
        } else {
            extendNowBtn.style.display = "none";
            if (btnCancel) btnCancel.style.display = "inline-block";
            if (linkCancel) linkCancel.style.display = "none";
        }
        
        if (coinRequest?.status === "expired") {
            if (state.readyToStart) {
                setExtendMessage("Time expired. Auto-extending with inserted coins...", "success");
                clearPolling();
                extendNowBtn.click();
            } else {
                clearPolling();
            }
        } else if (coinRequest?.status === "cancelled") {
            clearPolling();
        }
    };

    const pollRequestStatus = async () => {
        if (!state.requestId || state.pollInFlight) return;
        state.pollInFlight = true;
        const currentMac = macAddress || getMacAddress();
        try {
            const response = await fetch(
                `/api/session/start/request-status/?request_id=${encodeURIComponent(state.requestId)}&mac_address=${encodeURIComponent(currentMac)}`
            );
            const data = await parseJsonSafe(response);
            if (!state.requestId || state.requestId !== data?.coin_request?.id) {
                return;
            }
            if (!response.ok) {
                if (response.status === 404) {
                    clearPolling();
                    setExtendMessage("Coin request no longer exists. Please request again.", "warning");
                    setExtendMeta("");
                    extendNowBtn.disabled = true;
                    extendNowBtn.style.display = "none";
                    const actionsContainer = document.getElementById("coin-actions-container");
                    if (actionsContainer) actionsContainer.style.display = "none";
                    const btnCancel = document.getElementById("btn-cancel-coin-request");
                    const linkCancel = document.getElementById("link-cancel-coin-request");
                    if (btnCancel) btnCancel.style.display = "inline-block";
                    if (linkCancel) linkCancel.style.display = "none";
                    return;
                }
                setExtendMessage(data.error || "Unable to check status.", "warning");
                return;
            }
            applyCoinRequestState(data.coin_request);
        } catch (error) {
            setExtendMessage("Connection issue. Retrying...", "warning");
        } finally {
            state.pollInFlight = false;
        }
    };

    const startPolling = () => {
        clearPolling();
        state.pollTimer = setInterval(pollRequestStatus, 3000);
        pollRequestStatus();
    };

    // EXPORT TO GLOBAL SCOPE FOR GROUP PLAN FLOW ON EXTEND PAGE
    window.applyCoinRequestState = applyCoinRequestState;
    window.startPolling = startPolling;

    // Plan selection in extend grid (if outside rates modal)
    const extendCards = extendPlanGrid ? extendPlanGrid.querySelectorAll(".extend-plan-card") : [];
    extendCards.forEach((card) => {
        if (card.closest("#ratesModal")) return;
        card.addEventListener("click", () => {
            extendCards.forEach((c) => c.classList.remove("selected"));
            card.classList.add("selected");
            extendPlanInput.value = card.dataset.planId;
            extendRequestBtn.disabled = false;
            setTimeout(() => {
                extendRequestBtn.scrollIntoView({ behavior: "smooth", block: "center" });
            }, 80);

            // Always reset active request and buttons on card click
            clearPolling();
            state.requestId = null;
            state.readyToStart = false;
            state.isGroupPass = false;
            state.groupDevices = null;
            extendNowBtn.disabled = true;
            extendNowBtn.style.display = "none";
            const actionsContainer = document.getElementById("coin-actions-container");
            if (actionsContainer) actionsContainer.style.display = "none";
            const btnCancel = document.getElementById("btn-cancel-coin-request");
            const linkCancel = document.getElementById("link-cancel-coin-request");
            if (btnCancel) btnCancel.style.display = "inline-block";
            if (linkCancel) linkCancel.style.display = "none";
            setExtendMessage("", "info");
            setExtendMeta("");
            state.planId = Number(card.dataset.planId);
        });
    });

    // Request coin slot for extend
    extendRequestBtn.addEventListener("click", async () => {
        const planId = Number(extendPlanInput.value) || state.planId || null;
        const currentMac = macAddress || getMacAddress();
        if (!currentMac) {
            setExtendMessage("Device identity missing. Please refresh.", "warning");
            return;
        }

        extendRequestBtn.disabled = true;
        extendNowBtn.disabled = true;
        state.planId = planId;

        try {
            const bodyPayload = { mac_address: currentMac, is_group_pass: false };
            if (planId) {
                bodyPayload.plan_id = planId;
            }

            const response = await fetch("/api/session/start/request/", {
                method: "POST",
                headers: { "Content-Type": "application/json", "X-CSRFToken": getCSRFToken() },
                body: JSON.stringify(bodyPayload),
            });
            const data = await parseJsonSafe(response);

            if (!response.ok) {
                setExtendMessage(data.error || "Unable to create coin request.", "danger");
                setExtendMeta("");
                return;
            }

            if (data.coin_request) {
                applyCoinRequestState(data.coin_request);
                if (!data.coin_request.ready_to_start) {
                    startPolling();
                }
            } else {
                setExtendMessage(data.message || "Coin request created.", "info");
            }
        } catch (error) {
            setExtendMessage("Connection error.", "danger");
        } finally {
            extendRequestBtn.disabled = false;
        }
    });

    // Extend now button
    extendNowBtn.addEventListener("click", async () => {
        const planId = Number(extendPlanInput.value) || state.planId || null;
        const currentMac = macAddress || getMacAddress();
        if (!currentMac) {
            setExtendMessage("Device MAC missing. Please refresh.", "warning");
            return;
        }
        if (state.isGroupPass && !planId) {
            setExtendMessage("Select a plan first.", "warning");
            return;
        }
        if (!state.readyToStart) {
            setExtendMessage("Insert coins first.", "warning");
            return;
        }

        extendRequestBtn.disabled = true;
        extendNowBtn.disabled = true;

        try {
            const bodyPayload = {
                mac_address: currentMac,
                is_group_pass: state.isGroupPass,
                group_devices: state.groupDevices,
            };
            if (planId) {
                bodyPayload.plan_id = planId;
            }

            const response = await fetch("/api/session/extend-paid/", {
                method: "POST",
                headers: { "Content-Type": "application/json", "X-CSRFToken": getCSRFToken() },
                body: JSON.stringify(bodyPayload),
            });
            const data = await parseJsonSafe(response);

            if (response.ok) {
                clearPolling();
                state.requestId = null;
                state.readyToStart = false;
                const countdownContainer = document.getElementById("coin-countdown-container");
                if (countdownContainer) countdownContainer.style.display = "none";
                const actionsContainer = document.getElementById("coin-actions-container");
                if (actionsContainer) actionsContainer.style.display = "none";
                if (data.session_group) {
                    window.location.reload();
                    return;
                }
                setExtendMessage(data.message || "Session extended!", "success");
                setExtendMeta("");

                // Update timer with new remaining time
                if (window.sessionTimer && data.session) {
                    window.sessionTimer.setRemaining(
                        Number(data.session.time_remaining_seconds) || window.sessionTimer.remaining
                    );
                }

                // Update amount paid and duration display in real-time
                if (data.session) {
                    const amountEl = document.getElementById("session-amount-paid") || document.querySelector('.detail-row .detail-value');
                    if (amountEl) amountEl.textContent = `₱${data.session.amount_paid}`;
                    const durationEl = document.getElementById("session-duration-display");
                    if (durationEl) {
                        const mins = data.session.duration_minutes_purchased || 0;
                        if (mins >= 60) {
                            const hrs = Math.floor(mins / 60);
                            const remMins = mins % 60;
                            durationEl.textContent = remMins > 0 ? `${hrs} hr${hrs > 1 ? 's' : ''} ${remMins} min${remMins > 1 ? 's' : ''}` : `${hrs} hr${hrs > 1 ? 's' : ''}`;
                        } else {
                            durationEl.textContent = `${mins} mins`;
                        }
                    }
                }

                // Reset extend state
                state.requestId = null;
                state.planId = null;
                state.isGroupPass = false;
                state.groupDevices = null;
                state.readyToStart = false;
                if (extendCards.length > 0) extendCards.forEach((c) => c.classList.remove("selected"));
                if (extendPlanInput) extendPlanInput.value = "";
                extendRequestBtn.disabled = false;
                extendNowBtn.disabled = true;
                return;
            }

            if (response.status === 402 && data.coin_request) {
                applyCoinRequestState(data.coin_request);
                if (!data.coin_request.ready_to_start) {
                    startPolling();
                }
            }
            setExtendMessage(data.error || "Failed to extend session.", "danger");
        } catch (error) {
            setExtendMessage("Connection error while extending session.", "danger");
        } finally {
            extendRequestBtn.disabled = false;
            if (!state.readyToStart) {
                extendNowBtn.disabled = true;
            }
        }
    });
}


function pollSessionStatus(macAddress, intervalMs = 3000) {
    setInterval(async () => {
        try {
            const response = await fetch(
                `/api/session/status/?mac_address=${encodeURIComponent(macAddress)}`
            );
            const data = await response.json();

            if (data.status === "expired") {
                window.location.href = buildPortalUrl("/", macAddress, { expired: 1 });
                return;
            }

            // Real-time synchronization of Pause / Resume / Outage state
            const pauseWarningEl = document.getElementById("pause-duration-warning");
            const pauseBtn = document.getElementById("pause-btn");
            const timerEl = document.getElementById("session-timer");
            const connectionStatusEl = document.getElementById("connection-status");

            if (data.isp_outage || data.status === "paused") {
                if (window.sessionTimer && !window.sessionTimer.isPaused) {
                    window.sessionTimer.pause();
                }
                if (data.session && data.session.time_remaining_seconds !== undefined && window.sessionTimer) {
                    window.sessionTimer.serverSeconds = Math.max(0, data.session.time_remaining_seconds);
                    window.sessionTimer.update();
                }
                if (timerEl) {
                    timerEl.dataset.status = "paused";
                    timerEl.classList.remove("timer-green");
                    timerEl.classList.add("timer-amber");
                }
                if (connectionStatusEl) {
                    connectionStatusEl.innerHTML = '<span class="status-dot" style="background: var(--color-warning);"></span><span>Paused</span>';
                }
                if (pauseBtn) {
                    pauseBtn.classList.add("paused");
                    if (data.isp_outage) {
                        pauseBtn.innerHTML = '<svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor"><path d="M5.5 3.5A1.5 1.5 0 0 1 7 5v6a1.5 1.5 0 0 1-3 0V5a1.5 1.5 0 0 1 1.5-1.5zm5 0A1.5 1.5 0 0 1 12 5v6a1.5 1.5 0 0 1-3 0V5a1.5 1.5 0 0 1 1.5-1.5z"/></svg><span>Frozen (No Internet)</span>';
                        pauseBtn.disabled = true;
                    } else {
                        pauseBtn.innerHTML = '<svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor"><path d="m11.596 8.697-6.363 3.692c-.54.313-1.233-.066-1.233-.697V4.308c0-.63.692-1.01 1.233-.696l6.363 3.692a.802.802 0 0 1 0 1.393z"/></svg><span>Resume</span>';
                        pauseBtn.disabled = false;
                    }
                }
                if (pauseWarningEl) {
                    pauseWarningEl.style.display = "block";
                }
                if (data.isp_outage) {
                    _showIspOutageBanner(data.announcement);
                }
            } else if (data.status === "active" && !data.isp_outage) {
                if (window.sessionTimer && window.sessionTimer.isPaused) {
                    window.sessionTimer.resume();
                }
                if (data.session && data.session.time_remaining_seconds !== undefined && window.sessionTimer) {
                    window.sessionTimer.serverSeconds = Math.max(0, data.session.time_remaining_seconds);
                }
                if (timerEl) {
                    timerEl.dataset.status = "active";
                    timerEl.classList.remove("timer-amber");
                    timerEl.classList.add("timer-green");
                }
                if (connectionStatusEl) {
                    connectionStatusEl.innerHTML = '<span class="status-dot"></span><span>Connected</span>';
                }
                if (pauseBtn) {
                    pauseBtn.classList.remove("paused");
                    pauseBtn.innerHTML = '<svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor"><path d="M5.5 3.5A1.5 1.5 0 0 1 7 5v6a1.5 1.5 0 0 1-3 0V5a1.5 1.5 0 0 1 1.5-1.5zm5 0A1.5 1.5 0 0 1 12 5v6a1.5 1.5 0 0 1-3 0V5a1.5 1.5 0 0 1 1.5-1.5z"/></svg><span>Pause</span>';
                    pauseBtn.disabled = false;
                }
                if (pauseWarningEl) {
                    pauseWarningEl.style.display = "none";
                }
                _hideIspOutageBanner();
            }

            _updatePortalAnnouncement(data.announcement);
            
            if (data.group_max && data.group_redeemed !== undefined) {
                const groupStatusEl = document.getElementById("group-plan-status");
                if (groupStatusEl) {
                    groupStatusEl.innerText = `${data.group_redeemed} / ${data.group_max} slots redeemed`;
                }
                
                if (data.group_code_expires_at) {
                    const expiryTimerEl = document.getElementById("group-code-expiry-timer");
                    if (expiryTimerEl && !expiryTimerEl.getAttribute('data-expires')) {
                         expiryTimerEl.setAttribute('data-expires', data.group_code_expires_at);
                    }
                }
            }
        } catch (error) {
            console.error("Status poll error:", error);
        }
    }, intervalMs);
}

function _showIspOutageBanner(customText) {
    let el = document.getElementById('isp-outage-banner');
    if (!el) {
        el = document.createElement('div');
        el.id = 'isp-outage-banner';
        el.className = 'alert alert-danger animate-fadeIn';
        el.style.cssText = 'background: #fef2f2; color: #991b1b; border: 1.5px solid #f87171; padding: 10px 14px; border-radius: 10px; font-size: 12px; font-weight: 600; margin-bottom: 12px; text-align: center; box-shadow: 0 4px 12px rgba(239, 68, 68, 0.15);';
        const card = document.querySelector('.portal-card') || document.querySelector('.container') || document.body;
        if (card.firstChild) {
            card.insertBefore(el, card.firstChild);
        } else {
            card.appendChild(el);
        }
    }
    const msg = customText || "⚠️ Internet connection is temporarily interrupted. Your timer is FROZEN to protect your time!";
    el.innerHTML = `<i class="bi bi-wifi-off"></i> ${msg}`;
    el.style.display = 'block';
}

function _hideIspOutageBanner() {
    const el = document.getElementById('isp-outage-banner');
    if (el) el.remove();
}

function _updatePortalAnnouncement(announcementText) {
    let bar = document.getElementById('announcement-banner-top');
    if (announcementText && !announcementText.includes("temporarily interrupted by our ISP")) {
        if (!bar) {
            bar = document.createElement('div');
            bar.id = 'announcement-banner-top';
            bar.className = 'alert alert-info';
            bar.style.cssText = 'margin: 0 0 12px 0; font-size: 12.5px; border-radius: 8px; text-align: center;';
            const card = document.querySelector('.portal-card') || document.querySelector('.container');
            if (card) card.prepend(bar);
        }
        bar.innerHTML = `<i class="bi bi-megaphone-fill"></i> ${announcementText}`;
        bar.style.display = 'block';
    } else if (bar) {
        bar.style.display = 'none';
    }
}

function showFiveMinuteWarning() {
    if (document.getElementById("time-warning")) {
        return;
    }

    const warning = document.createElement("div");
    warning.id = "time-warning";
    warning.className = "warning-alert animate-fadeIn";
    warning.innerHTML = `
        <div class="warning-icon"><i class="bi bi-exclamation-triangle-fill"></i></div>
        <div class="warning-text">Your time is almost up!</div>
        <p class="text-small text-muted mt-xs">Insert more coins to extend your session</p>
    `;

    const container = document.querySelector(".portal-container");
    if (container) {
        container.insertBefore(warning, container.firstChild);
    }
}

function getCSRFToken() {
    const cookie = document.cookie
        .split(";")
        .find((item) => item.trim().startsWith("csrftoken="));
    return cookie ? cookie.split("=")[1] : "";
}

function normalizeMacAddress(value) {
    if (!value) {
        return "";
    }

    const normalized = value.toUpperCase().trim();
    return MAC_ADDRESS_RE.test(normalized) ? normalized : "";
}

function getMacAddress() {
    const wrapper = document.querySelector(".portal-wrapper");
    const timerEl = document.getElementById("session-timer");
    const urlMac = new URLSearchParams(window.location.search).get("mac");
    const storedMac = localStorage.getItem("iconnect_mac");

    // 1. Server-detected physical MAC from ARP table is the highest authority
    const serverMac = normalizeMacAddress(wrapper ? wrapper.dataset.mac : "") ||
                      normalizeMacAddress(timerEl ? timerEl.dataset.mac : "");
    if (serverMac) {
        localStorage.setItem("iconnect_mac", serverMac);
        return serverMac;
    }

    // 2. URL parameter fallback
    const urlNormalized = normalizeMacAddress(urlMac);
    if (urlNormalized) {
        localStorage.setItem("iconnect_mac", urlNormalized);
        return urlNormalized;
    }

    // 3. Stored localStorage fallback
    const storedNormalized = normalizeMacAddress(storedMac);
    if (storedNormalized) {
        return storedNormalized;
    }

    return "";
}

function buildPortalUrl(path, macAddress, extraParams = {}) {
    const url = new URL(path, window.location.origin);

    if (macAddress) {
        url.searchParams.set("mac", macAddress);
    }

    Object.entries(extraParams).forEach(([key, value]) => {
        if (value === null || value === undefined || value === "") {
            return;
        }
        url.searchParams.set(key, value);
    });

    return `${url.pathname}${url.search}`;
}

function initJoinGroupFlow(macAddress) {
    const btnShowJoin = document.getElementById("btn-show-join-group");
    const joinModal = document.getElementById("joinGroupModal");
    const btnCancelJoin = document.getElementById("btn-cancel-join");
    const btnSubmitJoin = document.getElementById("btn-submit-join");
    const joinCodeInput = document.getElementById("join-group-code");
    const joinError = document.getElementById("join-group-error");

    if (!btnShowJoin || !joinModal || !btnCancelJoin || !btnSubmitJoin || !joinCodeInput) {
        return;
    }

    btnShowJoin.addEventListener("click", () => {
        joinModal.style.display = "flex";
        joinCodeInput.value = "";
        if (joinError) joinError.style.display = "none";
        joinCodeInput.focus();
    });

    btnCancelJoin.addEventListener("click", () => {
        joinModal.style.display = "none";
    });

    btnSubmitJoin.addEventListener("click", async () => {
        const code = joinCodeInput.value.trim().toUpperCase();
        if (!code || (code.length !== 5 && code.length !== 6)) {
            if (joinError) {
                joinError.textContent = "Please enter a valid 5-character code.";
                joinError.style.display = "block";
            }
            return;
        }

        btnSubmitJoin.disabled = true;
        btnSubmitJoin.textContent = "Redeeming...";
        if (joinError) joinError.style.display = "none";

        try {
            const currentMac = macAddress || getMacAddress();
            const devName = typeof getDeviceName === 'function' ? getDeviceName() : "";
            const response = await fetch("/api/session/join-group/", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCSRFToken(),
                },
                body: JSON.stringify({
                    mac_address: currentMac,
                    group_code: code,
                    device_name: devName,
                }),
            });
            const data = await parseJsonSafe(response);

            if (response.ok) {
                window.location.href = buildPortalUrl("/session/", currentMac);
            } else {
                if (joinError) {
                    joinError.textContent = data.error || "Failed to redeem group pass.";
                    joinError.style.display = "block";
                }
            }
        } catch (error) {
            if (joinError) {
                joinError.textContent = "Network error while redeeming group pass.";
                joinError.style.display = "block";
            }
        } finally {
            btnSubmitJoin.disabled = false;
            btnSubmitJoin.textContent = "Redeem Code";
        }
    });
}

document.addEventListener("DOMContentLoaded", () => {
    const macAddress = getMacAddress();

    initPlanSelection();
    initProductionStartFlow(macAddress);
    initJoinGroupFlow(macAddress);
    initVoucherInput();
    initExtendSessionFlow(macAddress);
    initPortalRealtime();

    const timerEl = document.getElementById("session-timer");
    if (!timerEl) {
        return;
    }

    if (!macAddress) {
        window.location.href = buildPortalUrl("/", "", { mac_required: 1 });
        return;
    }

    const totalSeconds = parseInt(timerEl.dataset.seconds, 10) || 0;
    const initialStatus = timerEl.dataset.status || "active";
    window.sessionTimer = new SessionTimer("session-timer", totalSeconds);

    // Probe multiple connectivity check endpoints to force the OS / browser
    // to re-verify internet access and clear the "No Internet" indicator.
    // Chrome relies on Google's own endpoints, not local ones.
    if (initialStatus === "active") {
        const probeUrls = [
            "/generate_204",                                              // local (handled by Django)
            "http://connectivitycheck.gstatic.com/generate_204",          // Chrome / Android
            "http://clients3.google.com/generate_204",                    // Chrome fallback
            "http://www.msftconnecttest.com/connecttest.txt",             // Windows
        ];
        probeUrls.forEach(url => {
            try {
                fetch(url, { mode: "no-cors", cache: "no-store" }).catch(() => {});
            } catch (e) {}
        });
        // Retry after 3s in case iptables wasn't fully applied yet
        setTimeout(() => {
            probeUrls.forEach(url => {
                try {
                    fetch(url, { mode: "no-cors", cache: "no-store" }).catch(() => {});
                } catch (e) {}
            });
        }, 3000);
    }

    window.sessionTimer.onExpire = async () => {
        // Call status API immediately to trigger server-side iptables block
        try {
            await fetch(`/api/session/status/?mac_address=${encodeURIComponent(macAddress)}`);
        } catch (e) { /* ignore */ }
        _showExpiredModal(macAddress);
        setTimeout(() => {
            window.location.href = buildPortalUrl("/", macAddress, { expired: 1 });
        }, 2500);
    };

    // If paused, don't start the countdown
    if (initialStatus !== "paused") {
        window.sessionTimer.start();
    } else {
        window.sessionTimer.update();
    }

    pollSessionStatus(macAddress);
    initPauseButton(macAddress);
});


function initPauseButton(macAddress) {
    const pauseBtn = document.getElementById("pause-btn");
    if (!pauseBtn) return;

    pauseBtn.addEventListener("click", async () => {
        pauseBtn.disabled = true;

        try {
            const response = await fetch("/api/session/pause/", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCSRFToken(),
                },
                body: JSON.stringify({ mac_address: macAddress }),
            });

            const data = await response.json();
            if (!response.ok) {
                console.error("Pause error:", data.error);
                alert(data.error || "Failed to toggle pause");
                return;
            }

            const timerEl = document.getElementById("session-timer");
            const connectionStatusEl = document.getElementById("connection-status");
            const pauseWarningEl = document.getElementById("pause-duration-warning");
            const pausesLeftEl = document.getElementById("pauses-left-display");

            if (data.status === "paused") {
                if (window.sessionTimer) {
                    window.sessionTimer.pause();
                    if (data.time_remaining_seconds !== undefined) {
                        window.sessionTimer.serverSeconds = Math.max(0, data.time_remaining_seconds);
                        window.sessionTimer.update();
                    }
                }
                if (timerEl) {
                    timerEl.dataset.status = "paused";
                    timerEl.classList.remove("timer-green");
                    timerEl.classList.add("timer-amber");
                }
                if (connectionStatusEl) {
                    connectionStatusEl.innerHTML = '<span class="status-dot" style="background: var(--color-warning);"></span><span>Paused</span>';
                }
                pauseBtn.classList.add("paused");
                pauseBtn.innerHTML = '<svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor"><path d="m11.596 8.697-6.363 3.692c-.54.313-1.233-.066-1.233-.697V4.308c0-.63.692-1.01 1.233-.696l6.363 3.692a.802.802 0 0 1 0 1.393z"/></svg><span>Resume</span>';
                if (pauseWarningEl) {
                    pauseWarningEl.style.display = "block";
                }
            } else if (data.status === "active") {
                if (window.sessionTimer) {
                    if (data.time_remaining_seconds !== undefined) {
                        window.sessionTimer.serverSeconds = Math.max(0, data.time_remaining_seconds);
                    }
                    window.sessionTimer.resume();
                }
                if (timerEl) {
                    timerEl.dataset.status = "active";
                    timerEl.classList.remove("timer-amber");
                    timerEl.classList.add("timer-green");
                }
                if (connectionStatusEl) {
                    connectionStatusEl.innerHTML = '<span class="status-dot"></span><span>Connected</span>';
                }
                pauseBtn.classList.remove("paused");
                pauseBtn.innerHTML = '<svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor"><path d="M5.5 3.5A1.5 1.5 0 0 1 7 5v6a1.5 1.5 0 0 1-3 0V5a1.5 1.5 0 0 1 1.5-1.5zm5 0A1.5 1.5 0 0 1 12 5v6a1.5 1.5 0 0 1-3 0V5a1.5 1.5 0 0 1 1.5-1.5z"/></svg><span>Pause</span>';
                if (pauseWarningEl) {
                    pauseWarningEl.style.display = "none";
                }
            }

            if (data.pauses_left !== undefined && pausesLeftEl) {
                pausesLeftEl.innerText = data.pauses_left;
            }
        } catch (err) {
            console.error("Pause toggle error:", err);
        } finally {
            pauseBtn.disabled = false;
        }
    });
}


// --- Group Plan Logic ---
const btnGroupPlan = document.getElementById("btn-group-plan");
const groupPlanModal = document.getElementById("groupPlanModal");
const btnCancelGroupPlan = document.getElementById("btn-cancel-group-plan");
const gpMinus = document.getElementById("gp-minus");
const gpPlus = document.getElementById("gp-plus");
const gpDeviceCount = document.getElementById("gp-device-count");
const groupPlanSelect = document.getElementById("group-plan-select");
const groupPlanPrice = document.getElementById("group-plan-price");
const btnGroupRequestSlot = document.getElementById("btn-group-request-slot");

let currentGpDevices = 2; // Default

function updateGroupPlanPrice() {
    if(!groupPlanSelect) return;
    const opt = groupPlanSelect.options[groupPlanSelect.selectedIndex];
    if(opt) {
        const price = parseInt(opt.getAttribute("data-price") || 0);
        const total = price * currentGpDevices;
        groupPlanPrice.innerText = "₱" + total;
    }
}

if (btnGroupPlan) {
    btnGroupPlan.addEventListener("click", () => {
        if(groupPlanModal) {
            groupPlanModal.style.display = "flex";
            updateGroupPlanPrice();
        }
    });
}

if (btnCancelGroupPlan) {
    btnCancelGroupPlan.addEventListener("click", () => {
        groupPlanModal.style.display = "none";
    });
}

if (gpMinus && gpPlus && gpDeviceCount) {
    gpMinus.addEventListener("click", () => {
        if (currentGpDevices > 2) {
            currentGpDevices--;
            gpDeviceCount.value = currentGpDevices;
            updateGroupPlanPrice();
        }
    });
    gpPlus.addEventListener("click", () => {
        const currentMax = parseInt(gpDeviceCount.getAttribute('data-max-slots')) || 50;
        if (currentGpDevices < currentMax) {
            currentGpDevices++;
            gpDeviceCount.value = currentGpDevices;
            updateGroupPlanPrice();
        } else {
            alert("No more slots available.");
        }
    });
    gpDeviceCount.addEventListener("input", (e) => {
        let val = parseInt(e.target.value);
        if (!isNaN(val)) {
            // Update price instantly as they type, but don't strictly clamp until blur 
            // so we don't break their typing flow (e.g. typing "1" before "5").
            currentGpDevices = val;
            updateGroupPlanPrice();
        }
    });
    gpDeviceCount.addEventListener("blur", (e) => {
        let val = parseInt(e.target.value);
        const currentMax = parseInt(gpDeviceCount.getAttribute('data-max-slots')) || 50;
        
        if (isNaN(val) || val < 2) val = 2;
        if (val > currentMax) {
            val = currentMax;
            alert("Maximum available slots is " + currentMax);
        }
        
        currentGpDevices = val;
        gpDeviceCount.value = currentGpDevices;
        updateGroupPlanPrice();
    });
}

if (groupPlanSelect) {
    groupPlanSelect.addEventListener("change", updateGroupPlanPrice);
}

if (btnGroupRequestSlot) {
    btnGroupRequestSlot.addEventListener("click", async () => {
        const planId = groupPlanSelect.value;
        if(!planId) {
            alert("Please select a plan.");
            return;
        }
        
        try {
            btnGroupRequestSlot.disabled = true;
            btnGroupRequestSlot.innerText = "Requesting...";
            
            const response = await fetch("/api/session/start/request/", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCSRFToken()
                },
                body: JSON.stringify({
                    mac_address: getMacAddress(),
                    plan_id: parseInt(planId),
                    is_group_pass: true,
                    group_pass_devices: currentGpDevices,
                    group_pass_duration_minutes: 0 // Duration will be pulled from plan on backend
                })
            });

            const data = await parseJsonSafe(response);
            if (response.ok) {
                if (groupPlanModal) groupPlanModal.style.display = "none";
                const startSessionBtn = document.getElementById("request-slot-btn") || document.getElementById("extend-request-btn");
                const btnShowJoinGroup = document.getElementById("btn-show-join-group");
                const planCards = document.querySelectorAll(".plan-card, .extend-plan-card");
                const flowMessage = document.getElementById("start-flow-message") || document.getElementById("extend-flow-message");
                const flowMeta = document.getElementById("start-flow-meta") || document.getElementById("extend-flow-meta");
                const extendPlanInput = document.getElementById("extend-plan");
                if (extendPlanInput) extendPlanInput.value = planId;
                
                if(startSessionBtn) startSessionBtn.disabled = true;
                if(btnShowJoinGroup) btnShowJoinGroup.disabled = true;
                planCards.forEach(c => c.style.pointerEvents = "none");
                
                if(flowMessage) {
                    flowMessage.style.display = "block";
                    flowMessage.className = "alert alert-warning";
                    flowMessage.innerHTML = `<strong>Insert coins now!</strong><br>Please insert exactly ₱${data.coin_request.expected_amount}.`;
                }
                btnGroupRequestSlot.disabled = false;
                btnGroupRequestSlot.innerHTML = `<i class="bi bi-coin"></i> Insert Coins 🪙`;

                if (window.applyCoinRequestState && window.startPolling) {
                    window.applyCoinRequestState(data.coin_request);
                    if (!data.coin_request.ready_to_start) {
                        window.startPolling();
                    }
                }

                // Smooth scroll to the flow message & action button
                setTimeout(() => {
                    const targetEl = document.getElementById("extend-flow-message") ||
                                     document.getElementById("start-flow-message") ||
                                     document.getElementById("extend-now-btn") ||
                                     document.getElementById("start-session-btn") ||
                                     document.getElementById("coin-countdown-container");
                    if (targetEl) {
                        targetEl.scrollIntoView({ behavior: "smooth", block: "center" });
                    }
                }, 80);
                
            } else {
                alert(data.error || "Failed to request coin slot.");
                btnGroupRequestSlot.disabled = false;
                btnGroupRequestSlot.innerHTML = `<i class="bi bi-coin"></i> Insert Coins 🪙`;
            }
        } catch (error) {
            console.error("Error requesting slot:", error);
            alert("Network error.");
            btnGroupRequestSlot.disabled = false;
            btnGroupRequestSlot.innerHTML = `<i class="bi bi-coin"></i> Insert Coins 🪙`;
        }
    });
}

const handleCancelCoinRequest = async (trigger) => {
    const confirmCancel = confirm("Are you sure you want to cancel the coin slot request?");
    if (!confirmCancel) return;
    
    if (trigger) {
        if (trigger.tagName === "BUTTON") {
            trigger.disabled = true;
            trigger.innerText = "Canceling...";
        } else {
            trigger.style.pointerEvents = "none";
            trigger.textContent = "Canceling...";
        }
    }
    
    try {
        await fetch("/api/session/start/cancel/", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": getCSRFToken()
            },
            body: JSON.stringify({ mac_address: getMacAddress() })
        });
    } catch (e) {
        console.error(e);
    }
    
    window.location.reload();
};

const btnCancelCoinRequest = document.getElementById("btn-cancel-coin-request");
if (btnCancelCoinRequest) {
    btnCancelCoinRequest.addEventListener("click", () => handleCancelCoinRequest(btnCancelCoinRequest));
}

const linkCancelCoinRequest = document.getElementById("link-cancel-coin-request");
if (linkCancelCoinRequest) {
    linkCancelCoinRequest.addEventListener("click", () => handleCancelCoinRequest(linkCancelCoinRequest));
}

// Global Rates Modal helpers
window.openRatesModal = function() {
    const modal = document.getElementById('ratesModal');
    if (modal) {
        modal.style.display = 'flex';
        document.body.classList.add('modal-open');
        document.documentElement.classList.add('modal-open');
        const card = modal.querySelector('.modal-card');
        if (card) card.scrollTop = 0;
    }
};

window.closeRatesModal = function() {
    const modal = document.getElementById('ratesModal');
    if (modal) {
        modal.style.display = 'none';
        document.body.classList.remove('modal-open');
        document.documentElement.classList.remove('modal-open');
    }
};

// Prevent mobile pull-to-refresh and scroll lockup inside modals
(function() {
    let activeModalCard = null;
    let lastTouchY = 0;
    let velocityY = 0;
    let lastTouchTime = 0;
    let momentumRaf = null;

    function isModalOpen() {
        return document.body.classList.contains('modal-open') ||
               document.documentElement.classList.contains('modal-open') ||
               Boolean(document.querySelector('.modal-overlay[style*="display: flex"]'));
    }

    document.addEventListener('touchstart', function(e) {
        if (momentumRaf) {
            cancelAnimationFrame(momentumRaf);
            momentumRaf = null;
        }

        if (isModalOpen()) {
            activeModalCard = e.target.closest('.modal-card');
            if (activeModalCard && e.touches.length === 1) {
                lastTouchY = e.touches[0].clientY;
                lastTouchTime = Date.now();
                velocityY = 0;
            } else {
                activeModalCard = null;
            }
        } else {
            activeModalCard = null;
        }
    }, { passive: true });

    document.addEventListener('touchmove', function(e) {
        if (!isModalOpen()) return;

        // While any modal is open, completely prevent background dragging and pull-to-refresh
        if (!activeModalCard || e.touches.length !== 1) {
            if (e.cancelable) e.preventDefault();
            return;
        }

        const currentY = e.touches[0].clientY;
        const stepDeltaY = currentY - lastTouchY;
        const now = Date.now();
        const dt = Math.max(1, now - lastTouchTime);
        velocityY = stepDeltaY / dt;
        lastTouchTime = now;
        lastTouchY = currentY;

        const maxScroll = activeModalCard.scrollHeight - activeModalCard.clientHeight;
        if (maxScroll > 0) {
            if (stepDeltaY > 0) {
                // Dragging finger DOWN (scrolling content UP towards top)
                if (activeModalCard.scrollTop > 0) {
                    if (e.cancelable) e.preventDefault();
                    activeModalCard.scrollTop = Math.max(0, activeModalCard.scrollTop - stepDeltaY);
                } else {
                    // Reached top boundary: block downward drag so WebView/Chrome never triggers pull-to-refresh
                    if (e.cancelable) e.preventDefault();
                }
            } else if (stepDeltaY < 0) {
                // Dragging finger UP (scrolling content DOWN towards bottom)
                if (activeModalCard.scrollTop < maxScroll) {
                    if (e.cancelable) e.preventDefault();
                    activeModalCard.scrollTop = Math.min(maxScroll, activeModalCard.scrollTop - stepDeltaY);
                } else {
                    // Reached bottom boundary: block upward overscroll
                    if (e.cancelable) e.preventDefault();
                }
            }
        } else {
            // Content fits in card without scrolling: block all dragging
            if (e.cancelable) e.preventDefault();
        }
    }, { passive: false });

    document.addEventListener('touchend', function(e) {
        if (!isModalOpen() || !activeModalCard) return;

        // Apply smooth momentum scrolling if flicked
        if (Math.abs(velocityY) > 0.15) {
            const card = activeModalCard;
            let currentV = velocityY * 16;
            const friction = 0.94;

            function momentumStep() {
                if (Math.abs(currentV) < 0.5) return;
                const maxScroll = card.scrollHeight - card.clientHeight;
                if (maxScroll <= 0) return;

                const nextScroll = card.scrollTop - currentV;
                card.scrollTop = Math.max(0, Math.min(maxScroll, nextScroll));
                currentV *= friction;

                if (card.scrollTop > 0 && card.scrollTop < maxScroll) {
                    momentumRaf = requestAnimationFrame(momentumStep);
                }
            }
            momentumRaf = requestAnimationFrame(momentumStep);
        }
    }, { passive: true });
})();

