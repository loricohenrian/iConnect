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
COIN_RELAY_PIN = int(os.getenv("COIN_RELAY_PIN", os.getenv("RELAY_PIN", "0")))
RELAY_ACTIVE_HIGH = os.getenv("RELAY_ACTIVE_HIGH", "True").lower() in ("true", "1", "yes")
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

# Global coinslot gating state
is_slot_active = False
active_mac = None
active_request_id = None
_stop_thread = False


def device_scope_active():
    return DEVICE_SCOPE_ENABLED and bool(DEVICE_MAC)


def poll_coinslot_status():
    """Background loop that checks if an active request has unlocked the coin slot."""
    global is_slot_active, active_mac, active_request_id, _stop_thread
    last_logged_state = None

    while not _stop_thread:
        if device_scope_active():
            is_slot_active = True
            time.sleep(1.0)
            continue

        try:
            resp = requests.get(STATUS_ENDPOINT, timeout=2)
            if resp.status_code == 200:
                data = resp.json()
                enabled = bool(data.get("enabled"))
                req_id = data.get("active_request_id")
                mac = data.get("mac_address")
                rem_sec = data.get("remaining_seconds", 0)

                is_slot_active = enabled
                active_mac = mac
                active_request_id = req_id

                if is_slot_active != last_logged_state:
                    last_logged_state = is_slot_active
                    if is_slot_active:
                        logger.info(
                            "🔓 Coinslot UNLOCKED for MAC %s (Request #%s, %ds remaining)",
                            mac, req_id, rem_sec
                        )
                    else:
                        logger.info("🔒 Coinslot LOCKED (No active request)")

                # If a physical relay pin is configured, toggle the hardware pin
                if COIN_RELAY_PIN > 0:
                    try:
                        import OPi.GPIO as GPIO
                        if RELAY_ACTIVE_HIGH:
                            GPIO.output(COIN_RELAY_PIN, GPIO.HIGH if is_slot_active else GPIO.LOW)
                        else:
                            GPIO.output(COIN_RELAY_PIN, GPIO.LOW if is_slot_active else GPIO.HIGH)
                    except Exception as err:
                        logger.debug("Error setting relay GPIO: %s", err)

        except Exception as exc:
            logger.debug("Could not poll coinslot status: %s", exc)

        time.sleep(0.5)


def send_coin_event(amount, denomination):
    """Send coin insertion to Django API."""
    payload = {
        "amount": amount,
        "denomination": denomination,
    }
    if device_scope_active():
        payload["mac_address"] = DEVICE_MAC

    try:
        response = requests.post(
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


def run_gpio():
    """Hardware mode for Orange Pi / ALLAN H3."""
    global _stop_thread
    try:
        import OPi.GPIO as GPIO
    except ImportError:
        logger.critical(
            "OPi.GPIO library not installed. "
            "Install with: pip install OPi.GPIO. "
            "This script must run on the Orange Pi hardware."
        )
        sys.exit(1)

    logger.info("=" * 50)
    logger.info("iConnect Coin Detector — PRODUCTION MODE")
    logger.info("=" * 50)
    logger.info("GPIO Pulse Pin: %s", GPIO_PIN)
    if COIN_RELAY_PIN > 0:
        logger.info("Hardware Relay/Inhibit Pin: %s", COIN_RELAY_PIN)
    else:
        logger.info("Hardware Relay Pin: Disabled (Software Gating Active)")
    logger.info("API endpoint: %s", API_ENDPOINT)
    logger.info("Status endpoint: %s", STATUS_ENDPOINT)

    GPIO.setmode(GPIO.BOARD)
    GPIO.setup(GPIO_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    if COIN_RELAY_PIN > 0:
        initial_relay = GPIO.LOW if RELAY_ACTIVE_HIGH else GPIO.HIGH
        GPIO.setup(COIN_RELAY_PIN, GPIO.OUT, initial=initial_relay)

    # Start background polling thread for coinslot enable/disable state
    status_thread = threading.Thread(target=poll_coinslot_status, daemon=True)
    status_thread.start()

    logger.info("Listening for coin pulses...")

    pulse_count = 0
    last_pulse_time = 0

    try:
        while True:
            if GPIO.input(GPIO_PIN) == GPIO.LOW:
                pulse_count += 1
                last_pulse_time = time.time()
                logger.debug("Pulse #%d", pulse_count)

                while GPIO.input(GPIO_PIN) == GPIO.LOW:
                    time.sleep(0.01)

            if pulse_count > 0 and (time.time() - last_pulse_time) > PULSE_TIMEOUT:
                if pulse_count in (1, 5, 10, 20):
                    amount = pulse_count
                    if not is_slot_active and not device_scope_active():
                        logger.warning(
                            "⛔ ₱%d coin pulse received but coinslot is LOCKED (No active request). Ignoring pulse.",
                            amount
                        )
                    else:
                        logger.info("₱%d coin detected (%d pulses)", amount, pulse_count)
                        send_coin_event(amount, amount)
                else:
                    logger.warning("Invalid pulse count: %d. Ignoring.", pulse_count)

                pulse_count = 0

            time.sleep(0.01)
    except KeyboardInterrupt:
        logger.info("Shutting down coin detector...")
    finally:
        _stop_thread = True
        GPIO.cleanup()


if __name__ == "__main__":
    run_gpio()

