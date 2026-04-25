/**
 * iConnect portal JavaScript.
 * Handles plan selection, countdown, and voucher submission.
 */

const MAC_ADDRESS_RE = /^([0-9A-F]{2}:){5}[0-9A-F]{2}$/;

class SessionTimer {
    constructor(elementId, totalSeconds) {
        this.element = document.getElementById(elementId);
        this.totalSeconds = totalSeconds;
        this.remaining = totalSeconds;
        this.interval = null;
        this.onExpire = null;
        this.onWarning = null;
        this.warningShown = false;
    }

    start() {
        this.update();
        this.interval = setInterval(() => {
            this.remaining -= 1;
            this.update();

            if (this.remaining <= 300 && !this.warningShown) {
                this.warningShown = true;
                if (this.onWarning) {
                    this.onWarning();
                }
            }

            if (this.remaining <= 0) {
                this.stop();
                if (this.onExpire) {
                    this.onExpire();
                }
            }
        }, 1000);
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

        const safeRemaining = Math.max(0, this.remaining);
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
        }
    }

    setRemaining(seconds) {
        this.remaining = Math.max(0, seconds);
        this.warningShown = this.remaining <= 300;
        this.update();
    }
}

function initPlanSelection() {
    const planCards = document.querySelectorAll(".plan-card");
    const selectedPlanInput = document.getElementById("selected-plan");

    planCards.forEach((card) => {
        card.addEventListener("click", () => {
            planCards.forEach((item) => item.classList.remove("selected"));
            card.classList.add("selected");

            if (selectedPlanInput) {
                selectedPlanInput.value = card.dataset.planId;
            }

            const requestBtn = document.getElementById("request-slot-btn");
            if (requestBtn) {
                requestBtn.disabled = false;
            }

            if (typeof window.onPortalPlanSelected === "function") {
                window.onPortalPlanSelected(card.dataset.planId);
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
        .map((plan, index) => {
            const speedLabel = plan.speed_limit
                ? `${plan.speed_limit} Mbps`
                : "Full Speed";
            const popularBadge =
                index === 1
                    ? '<div class="plan-popular">Popular</div>'
                    : "";

            return `
                <div class="plan-card animate-fadeIn" data-plan-id="${plan.id}" id="plan-${plan.id}" style="animation-delay: ${index}00ms">
                    ${popularBadge}
                    <div class="plan-price">₱${plan.price}</div>
                    <div class="plan-duration">${escapeHtml(plan.duration_display)}</div>
                    <div class="plan-speed"><i class="bi bi-calculator"></i> ₱${Number(plan.price_per_minute).toFixed(2)}/min</div>
                    <div class="plan-speed"><i class="bi bi-speedometer2"></i> ${speedLabel}</div>
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

async function syncPortalLiveData() {
    try {
        const response = await fetch("/api/portal/live-data/", {
            headers: {
                "Cache-Control": "no-cache",
            },
        });

        if (!response.ok) {
            return;
        }

        const data = await response.json();
        renderAnnouncements(data.announcements || []);
        renderPlans(data.plans || []);
    } catch (error) {
        console.error("Live data sync error:", error);
    }
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

function setStartFlowMeta(metaText) {
    const metaEl = document.getElementById("start-flow-meta");
    if (!metaEl) {
        return;
    }
    metaEl.textContent = metaText || "";
}

function formatCoinRequestMeta(coinRequest) {
    if (!coinRequest) {
        return "";
    }

    const status = (coinRequest.status || "").toUpperCase();
    const queuePosition = Number(coinRequest.queue_position || 0);
    const credited = Number(coinRequest.credited_amount || 0);
    const expected = Number(coinRequest.expected_amount || 0);

    const parts = [];
    if (status) {
        parts.push(`Status: ${status}`);
    }
    if (queuePosition > 0) {
        parts.push(`Queue: #${queuePosition}`);
    }
    if (expected > 0) {
        parts.push(`Payment: ₱${credited} / ₱${expected}`);
    }

    return parts.join(" | ");
}

function coinRequestStatusMessage(coinRequest) {
    if (!coinRequest) {
        return "Unable to read coin request status.";
    }

    const status = coinRequest.status;
    if (status === "completed") {
        return "Payment complete. Tap Connect Now to start your session.";
    }
    if (status === "active") {
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

        if (state.readyToStart || ["expired", "cancelled"].includes(coinRequest?.status)) {
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
        const planId = selectedPlanId();
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
            const response = await fetch("/api/session/start/", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCSRFToken(),
                },
                body: JSON.stringify({
                    mac_address: macAddress,
                    plan_id: planId,
                }),
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
    const voucherInput = document.getElementById("voucher-input");
    const voucherBtn = document.getElementById("voucher-submit");

    if (!voucherInput || !voucherBtn) {
        return;
    }

    voucherInput.addEventListener("input", (event) => {
        event.target.value = event.target.value.toUpperCase();
    });

    voucherBtn.addEventListener("click", async () => {
        const code = voucherInput.value.trim();
        if (!code || code.length < 6) {
            showVoucherMessage("Enter a valid 6-character code", "error");
            return;
        }

        const macAddress = getMacAddress();
        if (!macAddress) {
            showVoucherMessage("Device identity missing. Re-open portal from WiFi login.", "error");
            return;
        }
        voucherBtn.disabled = true;
        voucherBtn.innerHTML = '<span class="spinner"></span>';

        try {
            const response = await fetch("/api/session/extend/", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": getCSRFToken(),
                },
                body: JSON.stringify({
                    voucher_code: code,
                    mac_address: macAddress,
                }),
            });
            const data = await response.json();

            if (!response.ok) {
                showVoucherMessage(data.error || "Invalid voucher code", "error");
                return;
            }

            showVoucherMessage(data.message, "success");
            voucherInput.value = "";

            if (window.sessionTimer && data.session) {
                window.sessionTimer.setRemaining(
                    Number(data.session.time_remaining_seconds) || window.sessionTimer.remaining
                );
            } else {
                setTimeout(() => {
                    window.location.href = buildPortalUrl("/session/", macAddress);
                }, 1000);
            }
        } catch (error) {
            showVoucherMessage("Connection error. Please try again.", "error");
        } finally {
            voucherBtn.disabled = false;
            voucherBtn.textContent = "Apply";
        }
    });
}

function showVoucherMessage(message, type) {
    const messageElement = document.getElementById("voucher-message");
    if (!messageElement) {
        return;
    }

    messageElement.textContent = message;
    messageElement.className =
        type === "success" ? "alert alert-success mt-sm" : "alert alert-danger mt-sm";
    messageElement.style.display = "block";

    setTimeout(() => {
        messageElement.style.display = "none";
    }, 5000);
}

function pollSessionStatus(macAddress, intervalMs = 10000) {
    setInterval(async () => {
        try {
            const response = await fetch(
                `/api/session/status/?mac_address=${encodeURIComponent(macAddress)}`
            );
            const data = await response.json();

            if (data.status === "expired") {
                window.location.href = buildPortalUrl("/", macAddress, { expired: 1 });
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

document.addEventListener("DOMContentLoaded", () => {
    const macAddress = getMacAddress();

    initPlanSelection();
    initProductionStartFlow(macAddress);
    initVoucherInput();
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
    window.sessionTimer = new SessionTimer("session-timer", totalSeconds);
    window.sessionTimer.onWarning = showFiveMinuteWarning;
    window.sessionTimer.onExpire = () => {
        window.location.href = buildPortalUrl("/", macAddress, { expired: 1 });
    };
    window.sessionTimer.start();
    pollSessionStatus(macAddress);
});
