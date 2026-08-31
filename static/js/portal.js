/**
 * iConnect portal JavaScript.
 * Handles plan selection, countdown, and voucher submission.
 */

const MAC_ADDRESS_RE = /^([0-9A-F]{2}:){5}[0-9A-F]{2}$/;

class SessionTimer {
    constructor(elementId, totalSeconds) {
        this.element = document.getElementById(elementId);
        this.serverSeconds = totalSeconds;
        this.startedAt = (typeof performance !== "undefined" && performance.now) ? performance.now() : Date.now();
        this.interval = null;
        this.onExpire = null;
        this.onWarning = null;
        this.warningShown = false;
        this.oneMinWarningShown = false;
        this.expiredNotified = false;
        this.isPaused = false;
    }

    get remaining() {
        if (this.isPaused) {
            return Math.max(0, this.serverSeconds);
        }
        const now = (typeof performance !== "undefined" && performance.now) ? performance.now() : Date.now();
        const elapsed = (now - this.startedAt) / 1000;
        return Math.max(0, this.serverSeconds - elapsed);
    }

    setRemaining(seconds) {
        this.serverSeconds = seconds;
        this.startedAt = (typeof performance !== "undefined" && performance.now) ? performance.now() : Date.now();
        this.isPaused = false;
        this.update();
    }

    pause() {
        this.isPaused = true;
        this.serverSeconds = this.remaining;
        this.stop();
        this.update();
        _hideTimerBanner();
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

    setRemaining(seconds) {
        this.serverSeconds = Math.max(0, seconds);
        this.startedAt = Date.now();
        this.warningShown = this.remaining <= 300;
        this.oneMinWarningShown = this.remaining <= 60;
        this.update();
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

function _sendBrowserNotification(title, body) {
    // Request permission and send browser notification
    if (!('Notification' in window)) return;

    if (Notification.permission === 'granted') {
        new Notification(title, { body, icon: '/static/images/favicon.png' });
    } else if (Notification.permission !== 'denied') {
        Notification.requestPermission().then(permission => {
            if (permission === 'granted') {
                new Notification(title, { body, icon: '/static/images/favicon.png' });
            }
        });
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

    planGrid.innerHTML = plans
        .map((plan) => {
            const popularBadge =
                plan.is_most_popular
                    ? '<div class="plan-popular">Popular</div>'
                    : "";

            const speedHtml = plan.speed_limit
                ? `<div class="plan-speed" style="font-size:12px;color:var(--text-secondary);margin-top:4px;text-transform:none;"><span style="text-transform:none">\u2193</span>${plan.speed_limit}${plan.speed_limit_upload ? ' / <span style="text-transform:none">\u2191</span>' + plan.speed_limit_upload : ''} Mbps</div>`
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
        if (requestBtn) {
            requestBtn.disabled = false;
        }
        if (startBtn && startBtn.dataset.readyToStart !== "1") {
            startBtn.disabled = true;
        }
    } else {
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
    }
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

        const intervalMs = document.hidden ? 60000 : 15000;
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
    if (expected > 0) {
        parts.push(`Payment: <strong>₱${credited} / ₱${expected}</strong>`);
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
            return "Minimum reached! Insert more coins for more time, or tap Connect Now.";
        }
        return "Insert coins now. Your device currently owns the coin slot window.";
    }
    if (status === "pending") {
        return "Request queued. Wait for your turn to insert coins.";
    }
    if (status === "expired") {
        return "Coin window expired. Tap Request Coin Slot again.";
    }
    if (status === "cancelled") {
        return "Coin request was cancelled. Request a new slot to continue.";
    }
    return "Coin request updated.";
}

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
        const planId = selectedPlanId();
        if (!planId) {
            setStartFlowMessage("Select a plan before requesting a coin slot.", "warning");
            return;
        }

        if (!macAddress) {
            setStartFlowMessage("Device identity missing. Re-open portal from WiFi login.", "danger");
            return;
        }

        requestBtn.disabled = true;
        startBtn.disabled = true;
        startBtn.dataset.readyToStart = "0";
        state.planId = planId;

        try {
            const response = await fetch("/api/session/start/request/", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCSRFToken(),
                },
                body: JSON.stringify({
                    mac_address: macAddress,
                    plan_id: planId,
                    is_group_pass: document.getElementById("group-pass-toggle")?.checked || false,
                    group_pass_devices: document.getElementById("group-pass-toggle")?.checked ? parseInt(document.getElementById("fp-device-count")?.innerText || "2") : null,
                }),
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
            requestBtn.disabled = !selectedPlanInput.value;
        }
    });

    startBtn.addEventListener("click", async () => {
        const planId = state.planId || selectedPlanId();
        if (!planId) {
            setStartFlowMessage("Select a plan first.", "warning");
            startBtn.disabled = true;
            startBtn.dataset.readyToStart = "0";
            return;
        }

        if (!state.readyToStart) {
            setStartFlowMessage("Insert enough coins first, then tap Connect Now.", "warning");
            return;
        }

        requestBtn.disabled = true;
        startBtn.disabled = true;

        try {
            const payload = {
                mac_address: macAddress,
                plan_id: planId
            };
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
            requestBtn.disabled = !selectedPlanInput.value;
            if (!state.readyToStart) {
                startBtn.disabled = true;
                startBtn.dataset.readyToStart = "0";
            }
        }
    });

    
    window.onPortalPlanSelected = (planIdValue) => {
        const nextPlanId = Number.parseInt(planIdValue, 10);
        if (!Number.isInteger(nextPlanId) || nextPlanId <= 0) {
            requestBtn.disabled = true;
            startBtn.disabled = true;
            startBtn.dataset.readyToStart = "0";
            return;
        }

        requestBtn.disabled = false;

        if (state.planId && state.planId !== nextPlanId) {
            clearPolling();
            state.requestId = null;
            state.readyToStart = false;
            startBtn.disabled = true;
            startBtn.dataset.readyToStart = "0";
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

    if (!extendPlanInput || !extendRequestBtn || !extendNowBtn || !extendPlanGrid) {
        return;
    }

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

        setExtendMessage(
            coinRequestStatusMessage(coinRequest, "extend"),
            state.readyToStart ? "success" :
                ["expired", "cancelled"].includes(coinRequest?.status) ? "warning" : "info"
        );
        setExtendMeta(formatCoinRequestMeta(coinRequest));

        extendNowBtn.disabled = !state.readyToStart;
        
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
        try {
            const response = await fetch(
                `/api/session/start/request-status/?request_id=${encodeURIComponent(state.requestId)}&mac_address=${encodeURIComponent(macAddress)}`
            );
            const data = await parseJsonSafe(response);
            if (!response.ok) {
                if (response.status === 404) {
                    clearPolling();
                    setExtendMessage("Coin request no longer exists. Please request again.", "warning");
                    setExtendMeta("");
                    extendNowBtn.disabled = true;
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

    // Plan selection in extend grid
    const extendCards = extendPlanGrid.querySelectorAll(".extend-plan-card");
    extendCards.forEach((card) => {
        card.addEventListener("click", () => {
            extendCards.forEach((c) => c.classList.remove("selected"));
            card.classList.add("selected");
            extendPlanInput.value = card.dataset.planId;
            extendRequestBtn.disabled = false;
            setTimeout(() => {
                extendRequestBtn.scrollIntoView({ behavior: "smooth", block: "center" });
            }, 80);

            if (state.planId && state.planId !== Number(card.dataset.planId)) {
                clearPolling();
                state.requestId = null;
                state.readyToStart = false;
                extendNowBtn.disabled = true;
                setExtendMessage("Plan changed. Request a new coin slot.", "info");
                setExtendMeta("");
            }
            state.planId = Number(card.dataset.planId);
        });
    });

    // Request coin slot for extend
    extendRequestBtn.addEventListener("click", async () => {
        const planId = Number(extendPlanInput.value);
        if (!planId) {
            setExtendMessage("Select a plan first.", "warning");
            return;
        }

        extendRequestBtn.disabled = true;
        extendNowBtn.disabled = true;

        try {
            const response = await fetch("/api/session/start/request/", {
                method: "POST",
                headers: { "Content-Type": "application/json", "X-CSRFToken": getCSRFToken() },
                body: JSON.stringify({ mac_address: macAddress, plan_id: planId }),
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
            extendRequestBtn.disabled = !extendPlanInput.value;
        }
    });

    // Extend now button
    extendNowBtn.addEventListener("click", async () => {
        const planId = Number(extendPlanInput.value);
        if (!planId || !state.readyToStart) {
            setExtendMessage("Insert coins first.", "warning");
            return;
        }

        extendRequestBtn.disabled = true;
        extendNowBtn.disabled = true;

        try {
            const response = await fetch("/api/session/extend-paid/", {
                method: "POST",
                headers: { "Content-Type": "application/json", "X-CSRFToken": getCSRFToken() },
                body: JSON.stringify({ mac_address: macAddress, plan_id: planId }),
            });
            const data = await parseJsonSafe(response);

            if (response.ok) {
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
                    const amountEl = document.querySelector('.detail-row .detail-value');
                    if (amountEl) amountEl.textContent = `₱${data.session.amount_paid}`;
                    const detailRows = document.querySelectorAll('.detail-row');
                    if (detailRows.length >= 2) {
                        const durationEl = detailRows[1].querySelector('.detail-value');
                        if (durationEl) durationEl.textContent = `${data.session.duration_minutes_purchased} mins`;
                    }
                }

                // Reset extend state
                state.requestId = null;
                state.readyToStart = false;
                extendCards.forEach((c) => c.classList.remove("selected"));
                extendPlanInput.value = "";
                extendRequestBtn.disabled = true;
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
            setExtendMessage("Connection error.", "danger");
        } finally {
            extendRequestBtn.disabled = !extendPlanInput.value;
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
            }
            
            if (data.group_max && data.group_redeemed !== undefined) {
                const groupStatusEl = document.getElementById("group-plan-status");
                if (groupStatusEl) {
                    groupStatusEl.innerText = `${data.group_redeemed} / ${data.group_max} slots redeemed`;
                }
                
                // If API returned live expiry update
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
    const timerEl = document.getElementById("session-timer");
    const wrapper = document.querySelector(".portal-wrapper");
    const urlMac = new URLSearchParams(window.location.search).get("mac");
    const storedMac = localStorage.getItem("iconnect_mac");

    const candidates = [
        timerEl ? timerEl.dataset.mac : "",
        wrapper ? wrapper.dataset.mac : "",
        urlMac,
        storedMac,
    ];

    for (const candidate of candidates) {
        const normalized = normalizeMacAddress(candidate);
        if (normalized) {
            localStorage.setItem("iconnect_mac", normalized);
            return normalized;
        }
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
        if (!code || code.length !== 6) {
            if (joinError) {
                joinError.textContent = "Please enter a valid 6-character code.";
                joinError.style.display = "block";
            }
            return;
        }

        btnSubmitJoin.disabled = true;
        btnSubmitJoin.textContent = "Redeeming...";
        if (joinError) joinError.style.display = "none";

        try {
            const currentMac = macAddress || getMacAddress();
            const response = await fetch("/api/session/join-group/", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCSRFToken(),
                },
                body: JSON.stringify({
                    mac_address: currentMac,
                    group_code: code,
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

    // Request browser notification permission early
    if ('Notification' in window && Notification.permission === 'default') {
        Notification.requestPermission();
    }

    window.sessionTimer = new SessionTimer("session-timer", totalSeconds);
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

    let isPaused = (document.getElementById("session-timer")?.dataset.status === "paused");

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
                return;
            }

            // Reload to ensure accurate 'Pauses remaining' count and UI state
            window.location.reload();
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
                btnGroupRequestSlot.innerHTML = `<i class="bi bi-hourglass-split"></i> Request Coin Slot`;

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
                btnGroupRequestSlot.innerHTML = `<i class="bi bi-hourglass-split"></i> Request Coin Slot`;
            }
        } catch (error) {
            console.error("Error requesting slot:", error);
            alert("Network error.");
            btnGroupRequestSlot.disabled = false;
            btnGroupRequestSlot.innerHTML = `<i class="bi bi-hourglass-split"></i> Request Coin Slot`;
        }
    });
}

const btnCancelCoinRequest = document.getElementById("btn-cancel-coin-request");
if (btnCancelCoinRequest) {
    btnCancelCoinRequest.addEventListener("click", async () => {
        const confirmCancel = confirm("Are you sure you want to cancel the coin slot request?");
        if (!confirmCancel) return;
        
        btnCancelCoinRequest.disabled = true;
        btnCancelCoinRequest.innerText = "Canceling...";
        
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
    });
}
