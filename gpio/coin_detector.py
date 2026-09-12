import os
import sys
import time
import logging
import threading
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("coin_detector")

DJANGO_URL = os.getenv("DJANGO_URL", "http://127.0.0.1")
GPIO_PIN = int(os.getenv("GPIO_PIN", "3"))
COIN_RELAY_PIN = int(os.getenv("COIN_RELAY_PIN", os.getenv("RELAY_PIN", "8")))
RELAY_ACTIVE_HIGH = os.getenv("RELAY_ACTIVE_HIGH", "True").lower() in ("true", "1", "yes")
GPIO_CHIP = os.getenv("GPIO_CHIP", "/dev/gpiochip1")

DEVICE_MAC = os.getenv("DEVICE_MAC", "").upper().strip()
DEVICE_SCOPE_ENABLED = os.getenv("DEVICE_SCOPE_ENABLED", "False").lower() in ("true", "1", "yes")
DEVICE_API_KEY = os.getenv("DEVICE_API_KEY", "iconnect-local-device-key-change-me")
if DEVICE_API_KEY in ("iconnect-local-device-key-change-me", "replace-with-a-strong-device-api-key", ""):
    secret_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.api_secret')
    if os.path.exists(secret_path):
        with open(secret_path, 'r') as f:
            DEVICE_API_KEY = f.read().strip()

PULSE_TIMEOUT = 0.5
API_ENDPOINT = f"{DJANGO_URL}/api/coin-inserted/"
STATUS_ENDPOINT = f"{DJANGO_URL}/api/coinslot/status/"

# Map header pin numbers to Zero 3 / H618 chip lines if needed
PIN_TO_LINE = {
    3: 229,  # Header Pin 3 -> Line 229
    8: 226,  # Header Pin 8 -> Line 226
    5: 228,  # Header Pin 5 -> Line 228 (Bill)
}
COIN_LINE = PIN_TO_LINE.get(GPIO_PIN, GPIO_PIN)
RELAY_LINE = PIN_TO_LINE.get(COIN_RELAY_PIN, COIN_RELAY_PIN)

is_slot_active = False
active_mac = None
active_request_id = None
_stop_thread = False
_http_session = requests.Session()
_gpiod_req = None


def device_scope_active():
    return DEVICE_SCOPE_ENABLED and bool(DEVICE_MAC)


def poll_coinslot_status():
    """Background loop that checks if an active request has unlocked the coin slot."""
    global is_slot_active, active_mac, active_request_id, _stop_thread, _gpiod_req
    last_logged_state = None

    while not _stop_thread:
        if device_scope_active():
            is_slot_active = True
            time.sleep(1.0)
            continue

        poll_interval = 1.0
        try:
            resp = _http_session.get(STATUS_ENDPOINT, timeout=2)
            if resp.status_code == 200:
                data = resp.json()
                enabled = bool(data.get("enabled"))
                req_id = data.get("active_request_id")
                mac = data.get("mac_address")
                rem_sec = data.get("remaining_seconds", 0)

                is_slot_active = enabled
                active_mac = mac
                active_request_id = req_id

                if is_slot_active:
                    poll_interval = 0.5

                if is_slot_active != last_logged_state:
                    last_logged_state = is_slot_active
                    if is_slot_active:
                        logger.info(
                            "🔓 Coinslot UNLOCKED for MAC %s (Request #%s, %ds remaining)",
                            mac, req_id, rem_sec
                        )
                    else:
                        logger.info("🔒 Coinslot LOCKED (No active request)")

                # Update relay output via gpiod if active
                if _gpiod_req and RELAY_LINE > 0:
                    try:
                        from gpiod.line import Value
                        target_val = Value.ACTIVE if (is_slot_active if RELAY_ACTIVE_HIGH else not is_slot_active) else Value.INACTIVE
                        _gpiod_req.set_value(RELAY_LINE, target_val)
                    except Exception as err:
                        logger.debug("Error setting relay gpiod: %s", err)

        except Exception as exc:
            logger.debug("Could not poll coinslot status: %s", exc)

        time.sleep(poll_interval)


def send_coin_event(amount, denomination):
    """Send coin insertion to Django API."""
    payload = {
        "amount": amount,
        "denomination": denomination,
    }
    if device_scope_active():
        payload["mac_address"] = DEVICE_MAC

    try:
        response = _http_session.post(
            API_ENDPOINT,
            json=payload,
            headers={"X-DEVICE-API-KEY": DEVICE_API_KEY},
            timeout=5,
        )
        data = response.json()
        if response.status_code == 201:
            logger.info("Server response: %s", data.get("message", "OK"))
            if data.get("voucher_code"):
                logger.info("Voucher code: %s", data["voucher_code"])
        elif response.status_code == 409:
            logger.warning("Coin rejected by server: %s", data.get("message"))
        else:
            logger.warning("Server returned %d: %s", response.status_code, data)
        return data
    except requests.exceptions.ConnectionError:
        logger.error("Cannot connect to Django at %s", DJANGO_URL)
        return None
    except Exception as exc:
        logger.error("Error sending coin event: %s", exc)
        return None


def run():
    global _stop_thread, _gpiod_req
    try:
        import gpiod
        from gpiod.line import Direction, Value, Edge, Bias
    except ImportError:
        logger.critical("gpiod library not installed. Run: pip install gpiod")
        sys.exit(1)

    logger.info("=" * 50)
    logger.info("iConnect Coin Detector (GPIOD Mode — Orange Pi Zero 3)")
    logger.info("=" * 50)
    logger.info("Chip: %s | Coin Line: %d | Relay Line: %d", GPIO_CHIP, COIN_LINE, RELAY_LINE)
    logger.info("API endpoint: %s", API_ENDPOINT)

    config = {
        COIN_LINE: gpiod.LineSettings(
            direction=Direction.INPUT,
            edge_detection=Edge.BOTH,
            bias=Bias.PULL_UP
        )
    }
    if RELAY_LINE > 0:
        initial_val = Value.ACTIVE if (not RELAY_ACTIVE_HIGH) else Value.INACTIVE
        config[RELAY_LINE] = gpiod.LineSettings(
            direction=Direction.OUTPUT,
            output_value=initial_val
        )

    # Start background polling thread for coinslot enable/disable state
    status_thread = threading.Thread(target=poll_coinslot_status, daemon=True)
    status_thread.start()

    logger.info("Listening for coin pulses on line %d...", COIN_LINE)

    pulse_count = 0
    last_pulse_time = 0

    with gpiod.request_lines(GPIO_CHIP, consumer="iconnect-coindetector", config=config) as req:
        _gpiod_req = req
        try:
            while True:
                # Check if pulse train completed
                now = time.time()
                if pulse_count > 0 and (now - last_pulse_time) > PULSE_TIMEOUT:
                    amount = pulse_count
                    if not is_slot_active and not device_scope_active():
                        logger.info("₱%d unassigned coin detected (%d pulses)", amount, pulse_count)
                    else:
                        logger.info("₱%d coin detected for active session (%d pulses)", amount, pulse_count)
                    send_coin_event(amount, amount)
                    pulse_count = 0

                # Wait for edge events with short timeout so we can check PULSE_TIMEOUT
                if req.wait_edge_events(timeout=0.05):
                    events = req.read_edge_events()
                    for ev in events:
                        if ev.line_offset == COIN_LINE:
                            if ev.event_type == ev.Type.FALLING_EDGE:
                                pulse_count += 1
                                last_pulse_time = time.time()
                                logger.info("Pulse detected! Total count: %d", pulse_count)
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        finally:
            _stop_thread = True


if __name__ == "__main__":
    run()

