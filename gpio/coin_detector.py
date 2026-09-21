import os
import sys
import time
import logging
import threading
import argparse
import glob
from contextlib import ExitStack
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("coin_detector")

DJANGO_URL = os.getenv("DJANGO_URL", "http://127.0.0.1")
GPIO_PIN = int(os.getenv("GPIO_PIN", "3"))
COIN_RELAY_PIN = int(os.getenv("COIN_RELAY_PIN", os.getenv("RELAY_PIN", "5")))
RELAY_ACTIVE_HIGH = os.getenv("RELAY_ACTIVE_HIGH", "True").lower() in ("true", "1", "yes")
GPIO_CHIP = os.getenv("GPIO_CHIP", "auto").strip() or "auto"

DEVICE_MAC = os.getenv("DEVICE_MAC", "").upper().strip()
DEVICE_SCOPE_ENABLED = os.getenv("DEVICE_SCOPE_ENABLED", "False").lower() in ("true", "1", "yes")
DEVICE_API_KEY = os.getenv("DEVICE_API_KEY", "iconnect-local-device-key-change-me")
if DEVICE_API_KEY in ("iconnect-local-device-key-change-me", "replace-with-a-strong-device-api-key", ""):
    secret_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.api_secret')
    if os.path.exists(secret_path):
        with open(secret_path, 'r') as f:
            DEVICE_API_KEY = f.read().strip()

PULSE_TIMEOUT = float(os.getenv("PULSE_TIMEOUT", "0.5"))
API_ENDPOINT = f"{DJANGO_URL}/api/coin-inserted/"
STATUS_ENDPOINT = f"{DJANGO_URL}/api/coinslot/status/"

# Orange Pi Zero 3 physical 26-pin header -> H618 global GPIO number.
# Source: the official Orange Pi Zero 3 26-pin interface table.
HEADER_PIN_TO_GPIO = {
    3: 229,  # PH5 / TWI3-SDA
    5: 228,  # PH4 / TWI3-SCK
    7: 73,   # PC9
    8: 226,  # PH2 / UART5-TX
    10: 227, # PH3 / UART5-RX
    11: 70,  # PC6
    12: 75,  # PC11
    13: 69,  # PC5
    15: 72,  # PC8
    16: 79,  # PC15
    18: 78,  # PC14
    19: 231, # PH7 / SPI1-MOSI
    21: 232, # PH8 / SPI1-MISO
    22: 71,  # PC7
    23: 230, # PH6 / SPI1-CLK
    24: 233, # PH9 / SPI1-CS
    26: 74,  # PC10
}

is_slot_active = False
active_mac = None
active_request_id = None
_stop_thread = False
_http_session = requests.Session()
_relay_req = None
_relay_line = None


def gpio_number_for_header_pin(header_pin):
    """Translate a physical Zero 3 header pin to its H618 GPIO number."""
    if header_pin > 26:
        # Backward compatibility for deployments that already store the
        # official/global H618 GPIO number instead of a physical pin number.
        return header_pin
    try:
        return HEADER_PIN_TO_GPIO[header_pin]
    except KeyError as exc:
        valid = ", ".join(str(pin) for pin in sorted(HEADER_PIN_TO_GPIO))
        raise ValueError(
            f"Unsupported physical header pin {header_pin}. GPIO-capable pins: {valid}"
        ) from exc


def _sysfs_gpiochips(sysfs_root="/sys/class/gpio", dev_root="/dev"):
    """Return (device, base, line-count) entries exposed by the kernel."""
    chips = []
    for chip_dir in sorted(glob.glob(os.path.join(sysfs_root, "gpiochip*"))):
        try:
            with open(os.path.join(chip_dir, "base"), encoding="ascii") as handle:
                base = int(handle.read().strip())
            with open(os.path.join(chip_dir, "ngpio"), encoding="ascii") as handle:
                line_count = int(handle.read().strip())
        except (OSError, ValueError):
            continue
        chips.append((os.path.join(dev_root, os.path.basename(chip_dir)), base, line_count))
    return chips


def resolve_gpio(gpiod_module, global_number, chip_hint="auto"):
    """Resolve a legacy/global GPIO number to a gpiochip-local line offset.

    libgpiod line offsets are local to a particular gpiochip. The previous
    implementation passed global lines 226/229 to gpiochip1, which commonly
    has far fewer lines and makes the detector exit before it can unlock the
    acceptor.
    """
    chip_hint = (chip_hint or "auto").strip()
    chip_ranges = _sysfs_gpiochips()

    for device, base, line_count in chip_ranges:
        if (
            chip_hint.lower() != "auto"
            and os.path.realpath(device) != os.path.realpath(chip_hint)
        ):
            continue
        if base <= global_number < base + line_count:
            return device, global_number - base

    candidates = (
        [chip_hint]
        if chip_hint.lower() != "auto"
        else sorted(glob.glob("/dev/gpiochip*"))
    )
    for device in candidates:
        if not device or not os.path.exists(device):
            continue
        try:
            with gpiod_module.Chip(device) as chip:
                line_count = chip.get_info().num_lines
            # Most H618 images expose the main controller at base zero, so its
            # official GPIO number is also the chip-local offset.
            if 0 <= global_number < line_count:
                return device, global_number
        except (OSError, AttributeError):
            continue

    detected = ", ".join(
        f"{device}[base={base}, lines={count}]" for device, base, count in chip_ranges
    ) or "none"
    raise RuntimeError(
        f"GPIO {global_number} was not found on {chip_hint!r}; detected gpiochips: {detected}. "
        "Run 'gpiodetect' and 'gpioinfo' to verify the kernel GPIO layout."
    )


def _set_relay(enabled):
    """Apply relay state and fail closed if status polling fails."""
    if not _relay_req or _relay_line is None:
        return
    from gpiod.line import Value
    logical_high = enabled if RELAY_ACTIVE_HIGH else not enabled
    _relay_req.set_value(_relay_line, Value.ACTIVE if logical_high else Value.INACTIVE)


def device_scope_active():
    return DEVICE_SCOPE_ENABLED and bool(DEVICE_MAC)


def poll_coinslot_status():
    """Background loop that checks if an active request has unlocked the coin slot."""
    global is_slot_active, active_mac, active_request_id, _stop_thread
    last_logged_state = None
    last_error = None

    while not _stop_thread:
        if device_scope_active():
            is_slot_active = True
            _set_relay(True)
            time.sleep(1.0)
            continue

        poll_interval = 1.0
        try:
            resp = _http_session.get(
                STATUS_ENDPOINT,
                headers={"X-DEVICE-API-KEY": DEVICE_API_KEY},
                timeout=2,
            )
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

                _set_relay(is_slot_active)
                last_error = None
            else:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")

        except Exception as exc:
            is_slot_active = False
            active_mac = None
            active_request_id = None
            try:
                _set_relay(False)
            except Exception as relay_exc:
                logger.error("Could not lock coin relay after status failure: %s", relay_exc)
            error_text = str(exc)
            if error_text != last_error:
                logger.error("Cannot read coin-slot status from %s: %s", STATUS_ENDPOINT, error_text)
                last_error = error_text

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


def load_gpiod():
    try:
        import gpiod
    except ImportError:
        logger.critical(
            "Python gpiod is not installed in this environment. Run: "
            "/opt/iconnect/pisowifi/.venv/bin/pip install -r requirements.txt"
        )
        return None
    return gpiod


def resolve_config(gpiod_module):
    coin_gpio = gpio_number_for_header_pin(GPIO_PIN)
    relay_gpio = gpio_number_for_header_pin(COIN_RELAY_PIN) if COIN_RELAY_PIN > 0 else None
    coin_chip, coin_line = resolve_gpio(gpiod_module, coin_gpio, GPIO_CHIP)
    relay_chip = relay_line = None
    if relay_gpio is not None:
        relay_chip, relay_line = resolve_gpio(gpiod_module, relay_gpio, GPIO_CHIP)
    return coin_gpio, coin_chip, coin_line, relay_gpio, relay_chip, relay_line


def diagnose():
    """Check software, GPIO mapping and Django communication without taking lines."""
    gpiod = load_gpiod()
    if not gpiod:
        return 1
    try:
        coin_gpio, coin_chip, coin_line, relay_gpio, relay_chip, relay_line = resolve_config(gpiod)
    except Exception as exc:
        logger.error("GPIO configuration FAILED: %s", exc)
        return 1

    logger.info("GPIO configuration OK")
    logger.info(
        "Coin input: configured pin %d -> GPIO %d -> %s line %d",
        GPIO_PIN,
        coin_gpio,
        coin_chip,
        coin_line,
    )
    if relay_gpio is not None:
        logger.info(
            "Coin relay: physical pin %d -> GPIO %d -> %s line %d (%s)",
            COIN_RELAY_PIN, relay_gpio, relay_chip, relay_line,
            "active-high" if RELAY_ACTIVE_HIGH else "active-low",
        )

    try:
        response = _http_session.get(
            STATUS_ENDPOINT,
            headers={"X-DEVICE-API-KEY": DEVICE_API_KEY},
            timeout=3,
        )
        response.raise_for_status()
        data = response.json()
        logger.info(
            "Django status API OK: enabled=%s active_request_id=%s remaining_seconds=%s",
            data.get("enabled"), data.get("active_request_id"), data.get("remaining_seconds"),
        )
    except Exception as exc:
        logger.error("Django status API FAILED (%s): %s", STATUS_ENDPOINT, exc)
        return 1
    return 0


def run():
    global _stop_thread, _relay_req, _relay_line
    gpiod = load_gpiod()
    if not gpiod:
        return 1
    from gpiod.line import Direction, Value, Edge, Bias

    try:
        coin_gpio, coin_chip, coin_line, relay_gpio, relay_chip, relay_line = resolve_config(gpiod)
    except Exception as exc:
        logger.critical("Invalid GPIO configuration: %s", exc)
        return 1

    logger.info("=" * 50)
    logger.info("iConnect Coin Detector (GPIOD Mode — Orange Pi Zero 3)")
    logger.info("=" * 50)
    logger.info("Coin: pin %d / GPIO %d -> %s line %d", GPIO_PIN, coin_gpio, coin_chip, coin_line)
    if relay_gpio is not None:
        logger.info("Relay: pin %d / GPIO %d -> %s line %d", COIN_RELAY_PIN, relay_gpio, relay_chip, relay_line)
    logger.info("API endpoint: %s", API_ENDPOINT)

    coin_config = {
        coin_line: gpiod.LineSettings(
            direction=Direction.INPUT,
            edge_detection=Edge.FALLING,
            bias=Bias.PULL_UP
        )
    }

    pulse_count = 0
    last_pulse_time = 0

    try:
        stack = ExitStack()
        with stack:
            coin_req = stack.enter_context(gpiod.request_lines(
                coin_chip,
                consumer="iconnect-coin-input",
                config=coin_config,
            ))
            if relay_gpio is not None:
                initial_val = Value.ACTIVE if not RELAY_ACTIVE_HIGH else Value.INACTIVE
                _relay_req = stack.enter_context(gpiod.request_lines(
                    relay_chip,
                    consumer="iconnect-coin-relay",
                    config={
                        relay_line: gpiod.LineSettings(
                            direction=Direction.OUTPUT,
                            output_value=initial_val,
                        )
                    },
                ))
                _relay_line = relay_line

            # Do not start status polling until both GPIO requests succeeded.
            status_thread = threading.Thread(target=poll_coinslot_status, daemon=True)
            status_thread.start()
            logger.info("Listening for coin pulses on %s line %d...", coin_chip, coin_line)

            while True:
                now = time.time()
                if pulse_count > 0 and (now - last_pulse_time) > PULSE_TIMEOUT:
                    amount = pulse_count
                    if amount not in (1, 5, 10, 20):
                        logger.warning("Invalid pulse count %d; coin ignored", pulse_count)
                    else:
                        if not is_slot_active and not device_scope_active():
                            logger.warning("Coin pulse detected while slot is locked: %d pulse(s)", pulse_count)
                        else:
                            logger.info("₱%d coin detected for active request (%d pulses)", amount, pulse_count)
                        send_coin_event(amount, amount)
                    pulse_count = 0

                if coin_req.wait_edge_events(timeout=0.05):
                    events = coin_req.read_edge_events()
                    for event in events:
                        if event.line_offset == coin_line:
                            pulse_count += 1
                            last_pulse_time = time.time()
                            logger.info("Pulse detected! Total count: %d", pulse_count)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as exc:
        logger.critical(
            "Coin detector could not claim or use the GPIO lines: %s. "
            "Run this file with --diagnose, then check 'gpioinfo' for a conflicting consumer.",
            exc,
        )
        return 1
    finally:
        _stop_thread = True
        _relay_req = None
        _relay_line = None
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="iConnect Orange Pi coin detector")
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="check GPIO mapping and the Django status endpoint without claiming GPIO lines",
    )
    args = parser.parse_args()
    sys.exit(diagnose() if args.diagnose else run())
